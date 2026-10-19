package lab.lambdalith;

import com.fasterxml.jackson.annotation.JsonProperty;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Optional;
import java.util.TreeSet;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import lab.lambdalith.Requests.ComputeRequest;
import lab.lambdalith.Requests.TransformRecord;
import lab.lambdalith.Requests.TransformRequest;

/**
 * 外部I/Oを含まない変換・計算・集約処理と、保存先からの読み取り手順を定義する。
 *
 * <p>同じ入力に対して他の言語の実装と同じ出力を返すため、並び順、ハッシュ入力、未登録IDの選び方を決定的にする。
 * IDとタグはASCIIのため、Stringの自然順序はUTF-8バイト順と一致する。
 */
public final class Domain {

  /** 正規化・ID整列済みレコードと、その件数。 */
  public record TransformResponse(List<TransformRecord> records, int count) {}

  /** 実際の処理条件と最終チェックサム。 */
  public record ComputeResponse(long processed, long iterations, String checksum) {}

  /** ID順で集約した件数、合計値、payloadチェックサム。 */
  public record AggregateResponse(
      int count, @JsonProperty("total_value") long totalValue, String checksum) {}

  private Domain() {}

  public static TransformResponse transform(TransformRequest request) {
    List<TransformRecord> records =
        request.records().stream()
            .map(
                record ->
                    new TransformRecord(
                        record.id(),
                        // 一般的な空白ではなくU+0020 SPACEだけを取り除き、名前を小文字へ正規化する。
                        trimSpaces(record.name()).toLowerCase(java.util.Locale.ROOT),
                        // タグの重複を除去し、入力順に依存しない昇順の一覧にする。
                        List.copyOf(new TreeSet<>(record.tags())),
                        record.value()))
            // 入力順に関係なく同じレスポンスを返すため、全レコードをID昇順に統一する。
            .sorted(Comparator.comparing(TransformRecord::id))
            .toList();
    return new TransformResponse(records, records.size());
  }

  public static ComputeResponse compute(ComputeRequest request) {
    // 全項目のダイジェストを最後にまとめてハッシュ化するための集約器を用意する。
    MessageDigest finalHash = sha256();
    MessageDigest itemHash = sha256();
    // 項目数の範囲で、seedと添字から独立した初期値を作り、指定回数だけSHA-256を適用する。
    for (long index = 0; index < request.items(); index++) {
      byte[] digest =
          itemHash.digest((request.seed() + ":" + index).getBytes(StandardCharsets.UTF_8));
      // 二回目以降は、直前の32バイトの生のダイジェストを入力にする。
      for (long round = 1; round < request.iterations(); round++) {
        digest = itemHash.digest(digest);
      }
      finalHash.update(digest);
    }
    return new ComputeResponse(
        request.items(), request.iterations(), HexFormat.of().formatHex(finalHash.digest()));
  }

  public static Item getItem(ItemRepository repository, String id) {
    // 保存先が成功しても項目がない場合は、未登録として404を返す。
    return readOrDependencyError(repository, id).orElseThrow(() -> AppError.notFound(id));
  }

  public static AggregateResponse aggregateSequential(ItemRepository repository, List<String> ids) {
    List<Item> found = new ArrayList<>();
    List<String> missing = new ArrayList<>();
    // ID順に一件ずつ読み、見つかった項目と未登録IDに分ける。
    for (String id : ids) {
      readOrDependencyError(repository, id).ifPresentOrElse(found::add, () -> missing.add(id));
    }
    return aggregate(found, missing);
  }

  public static AggregateResponse aggregateConcurrent(ItemRepository repository, List<String> ids) {
    // 全IDの読み取りを仮想スレッドで同時に開始し、直列方式と同じくIDごとに一回だけ外部I/Oを行う。
    try (ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor()) {
      List<Future<Optional<Item>>> reads =
          ids.stream().map(id -> executor.submit(() -> readOrDependencyError(repository, id))).toList();
      List<Item> found = new ArrayList<>();
      List<String> missing = new ArrayList<>();
      for (int index = 0; index < ids.size(); index++) {
        Optional<Item> item = await(reads.get(index));
        if (item.isPresent()) {
          found.add(item.get());
        } else {
          missing.add(ids.get(index));
        }
      }
      return aggregate(found, missing);
    }
  }

  /**
   * 読み取り結果を、実行方式に依存しない共通の集約結果へ正規化する。
   *
   * <p>未登録IDは完了順ではなく辞書順で選び、直列・並行のどちらでも同じ失敗応答にする。
   */
  public static AggregateResponse aggregate(List<Item> items, List<String> missing) {
    // 未登録IDが一件以上ある場合は、辞書順で最初のIDを404として返す。
    Optional<String> firstMissing = missing.stream().min(Comparator.naturalOrder());
    if (firstMissing.isPresent()) {
      throw AppError.notFound(firstMissing.get());
    }
    // 全項目が存在する場合だけ、ID順を固定して値の合計とpayload連結順を決定する。
    List<Item> ordered = items.stream().sorted(Comparator.comparing(Item::id)).toList();
    MessageDigest checksum = sha256();
    long totalValue = 0;
    for (Item item : ordered) {
      try {
        totalValue = Math.addExact(totalValue, item.value());
      } catch (ArithmeticException overflow) {
        // 合計がlongの範囲を超える場合は、誤った合計を返さず内部エラーとして処理を止める。
        throw AppError.internal();
      }
      checksum.update(item.payload().getBytes(StandardCharsets.UTF_8));
    }
    return new AggregateResponse(
        ordered.size(), totalValue, HexFormat.of().formatHex(checksum.digest()));
  }

  private static Optional<Item> readOrDependencyError(ItemRepository repository, String id) {
    try {
      return repository.get(id);
    } catch (RuntimeException error) {
      // 保存先の呼び出し失敗は、利用者の入力ではなく外部依存の障害として502にする。
      throw AppError.dependency();
    }
  }

  private static <T> T await(Future<T> future) {
    try {
      return future.get();
    } catch (ExecutionException error) {
      // 読み取り側で契約上のエラーに変換済みならそのまま伝え、それ以外は内部エラーとする。
      if (error.getCause() instanceof AppError appError) {
        throw appError;
      }
      throw AppError.internal();
    } catch (InterruptedException error) {
      Thread.currentThread().interrupt();
      throw AppError.internal();
    }
  }

  private static String trimSpaces(String text) {
    int start = 0;
    int end = text.length();
    while (start < end && text.charAt(start) == ' ') {
      start++;
    }
    while (end > start && text.charAt(end - 1) == ' ') {
      end--;
    }
    return text.substring(start, end);
  }

  private static MessageDigest sha256() {
    try {
      return MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException error) {
      // SHA-256はJava標準で必ず提供されるため、ここへ来るのは実行環境の異常だけである。
      throw new IllegalStateException(error);
    }
  }
}
