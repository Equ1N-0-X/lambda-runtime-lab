package lab.lambdalith;

/** 検証失敗のフィールド位置と、契約で定めた理由の組。 */
public record ErrorDetail(String field, String reason) {}
