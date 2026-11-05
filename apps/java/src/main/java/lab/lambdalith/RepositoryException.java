package lab.lambdalith;

import java.util.Set;
import software.amazon.awssdk.awscore.exception.AwsServiceException;

/**
 * 保存先の通信失敗または保存データ不正を、原因の分類とともに伝える。
 *
 * <p>分類（permission・table・throttling・network・invalid_item・unknown）とAWSのエラーコードは
 * 調査用の内部ログにだけ残し、公開する応答には含めない。
 */
public final class RepositoryException extends RuntimeException {

  private static final Set<String> PERMISSION_CODES =
      Set.of(
          "AccessDeniedException",
          "UnrecognizedClientException",
          "ExpiredTokenException",
          "InvalidSignatureException");
  private static final Set<String> THROTTLING_CODES =
      Set.of(
          "ProvisionedThroughputExceededException", "ThrottlingException", "RequestLimitExceeded");

  private final String category;
  private final String errorCode;

  RepositoryException(String category, String errorCode) {
    super(category);
    this.category = category;
    this.errorCode = errorCode;
  }

  static RepositoryException invalidItem() {
    return new RepositoryException("invalid_item", null);
  }

  static RepositoryException network() {
    return new RepositoryException("network", null);
  }

  /** AWSのエラーコードから、権限・テーブル・流量制限・その他の原因に分類する。 */
  static RepositoryException classify(AwsServiceException error) {
    String code = error.awsErrorDetails() == null ? null : error.awsErrorDetails().errorCode();
    String category;
    // 認証・権限の問題、テーブルの不在、流量制限は、それぞれ対処が異なるため分けて残す。
    if (code != null && PERMISSION_CODES.contains(code)) {
      category = "permission";
    } else if ("ResourceNotFoundException".equals(code)) {
      category = "table";
    } else if (code != null && THROTTLING_CODES.contains(code)) {
      category = "throttling";
    } else {
      category = "unknown";
    }
    return new RepositoryException(category, code);
  }

  public String category() {
    return category;
  }

  public String errorCode() {
    return errorCode;
  }
}
