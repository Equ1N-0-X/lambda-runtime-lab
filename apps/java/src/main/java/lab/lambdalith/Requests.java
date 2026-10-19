package lab.lambdalith;

import java.util.List;

/** 検証済みのリクエスト本文。検証層がデコード済みJSONから組み立てる。 */
public final class Requests {

  private Requests() {}

  /** ID・名前・タグ・数値を持つ変換前の一レコード。 */
  public record TransformRecord(String id, String name, List<String> tags, long value) {}

  /** 変換対象レコードの一覧。 */
  public record TransformRequest(List<TransformRecord> records) {}

  /** 項目数、反復回数、seed、実行方式からなるCPU計算条件。 */
  public record ComputeRequest(long items, long iterations, String seed, String profile) {}

  /** 重複しないID一覧と、直列・並行の読み取り方式。 */
  public record AggregateRequest(List<String> ids, String profile) {}
}
