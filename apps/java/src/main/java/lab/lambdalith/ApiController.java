package lab.lambdalith;

import jakarta.servlet.http.HttpServletRequest;
import java.util.Map;
import lab.lambdalith.Requests.AggregateRequest;
import lab.lambdalith.Requests.ComputeRequest;
import lab.lambdalith.Requests.TransformRequest;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

/**
 * HTTP入力を共通API契約のドメイン処理へ接続する。
 *
 * <p>本文はフレームワークの自動変換に任せず、生のバイト列を厳密なJSON解析と契約どおりの検証に通す。
 * 検証順序（計測相関ヘッダー → JSON解析 → 構造検証 → 業務検証）は他の言語の実装と同じにする。
 */
@RestController
public class ApiController {

  static final String BENCHMARK_RUN_ID_HEADER = "X-Benchmark-Run-Id";

  private final ItemRepository repository;

  public ApiController(ItemRepository repository) {
    this.repository = repository;
  }

  @GetMapping("/health")
  public Map<String, String> health(
      @RequestHeader(name = BENCHMARK_RUN_ID_HEADER, required = false) String runId,
      HttpServletRequest request) {
    // 計測IDだけを検証し、外部I/OなしでLambdaの到達性を返す。
    RequestLoggingFilter.setProfile(request, "not_applicable");
    checkBenchmarkRunId(runId);
    return Map.of("status", "ok");
  }

  @PostMapping("/transform")
  public Domain.TransformResponse transform(
      @RequestHeader(name = BENCHMARK_RUN_ID_HEADER, required = false) String runId,
      @RequestBody(required = false) byte[] body,
      HttpServletRequest request) {
    RequestLoggingFilter.setProfile(request, "not_applicable");
    checkBenchmarkRunId(runId);
    TransformRequest parsed = Validation.transformRequest(StrictJson.parse(bytes(body)));
    Validation.checkTransformSemantics(parsed);
    return Domain.transform(parsed);
  }

  @PostMapping("/compute")
  public Domain.ComputeResponse compute(
      @RequestHeader(name = BENCHMARK_RUN_ID_HEADER, required = false) String runId,
      @RequestBody(required = false) byte[] body,
      HttpServletRequest request) {
    // 検証に成功した計算プロファイルをログ用に保存し、指定されたCPU計算を実行する。
    checkBenchmarkRunId(runId);
    ComputeRequest parsed = Validation.computeRequest(StrictJson.parse(bytes(body)));
    RequestLoggingFilter.setProfile(request, parsed.profile());
    Validation.checkComputeSemantics(parsed);
    return Domain.compute(parsed);
  }

  @GetMapping("/items/{id}")
  public ResponseEntity<Map<String, Item>> item(
      @RequestHeader(name = BENCHMARK_RUN_ID_HEADER, required = false) String runId,
      @PathVariable String id,
      HttpServletRequest request) {
    // パスIDを検証してから一件だけ取得し、保存先障害と未登録をHTTPエラーへ変換する。
    RequestLoggingFilter.setProfile(request, "not_applicable");
    checkBenchmarkRunId(runId);
    Validation.checkItemId(id);
    return ResponseEntity.ok(Map.of("item", Domain.getItem(repository, id)));
  }

  @PostMapping("/aggregate")
  public Domain.AggregateResponse aggregate(
      @RequestHeader(name = BENCHMARK_RUN_ID_HEADER, required = false) String runId,
      @RequestBody(required = false) byte[] body,
      HttpServletRequest request) {
    checkBenchmarkRunId(runId);
    AggregateRequest parsed = Validation.aggregateRequest(StrictJson.parse(bytes(body)));
    RequestLoggingFilter.setProfile(request, parsed.profile());
    Validation.checkAggregateSemantics(parsed);
    // profile が sequential の場合はID順に読み、concurrent の場合は全IDを同時に読み取る。
    // どちらもIDごとの読み取り回数は一回に固定し、比較条件を揃える。
    return "sequential".equals(parsed.profile())
        ? Domain.aggregateSequential(repository, parsed.ids())
        : Domain.aggregateConcurrent(repository, parsed.ids());
  }

  /** 本文を読む前に計測相関ヘッダーを検証する。未指定なら通常リクエストとして通す。 */
  private static void checkBenchmarkRunId(String runId) {
    if (runId != null) {
      Validation.checkBenchmarkRunId(runId);
    }
  }

  private static byte[] bytes(byte[] body) {
    // 本文がない要求は空のバイト列として解析させ、invalid_jsonにする。
    return body == null ? new byte[0] : body;
  }
}
