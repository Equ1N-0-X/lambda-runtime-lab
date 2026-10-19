package lab.lambdalith;

import com.amazonaws.serverless.proxy.RequestReader;
import com.amazonaws.services.lambda.runtime.Context;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.PrintStream;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.servlet.HandlerMapping;
import tools.jackson.databind.json.JsonMapper;

/**
 * 計測とLambdaの実行記録を結び付けるための、一リクエスト一行の構造化ログを出力する。
 *
 * <p>他の言語と同じく標準出力へJSONを一行で直接書き、相関項目をトップレベルに置く。
 * 未登録の経路も含めて全リクエストを記録するため、ハンドラーではなくフィルターで出力する。
 */
public class RequestLoggingFilter extends OncePerRequestFilter {

  static final String BENCHMARK_REQUEST_INDEX_HEADER = "X-Benchmark-Request-Index";
  private static final String PROFILE_ATTRIBUTE = RequestLoggingFilter.class.getName() + ".profile";
  private static final JsonMapper MAPPER = JsonMapper.builder().build();

  private final PrintStream output;

  public RequestLoggingFilter() {
    this(System.out);
  }

  RequestLoggingFilter(PrintStream output) {
    this.output = output;
  }

  /** ログに残す計測プロファイルを、処理中のリクエストへ記録する。 */
  static void setProfile(HttpServletRequest request, String profile) {
    request.setAttribute(PROFILE_ATTRIBUTE, profile);
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    try {
      chain.doFilter(request, response);
    } finally {
      output.println(MAPPER.writeValueAsString(logLine(request, response.getStatus())));
    }
  }

  private static Map<String, Object> logLine(HttpServletRequest request, int status) {
    // 502は外部依存先の障害としてWARN、それ以外の500番台は内部障害としてERROR、
    // それ以外は仕様どおり応答できた処理としてINFOに分類する。全分類で相関項目は共通に保つ。
    String level = status == 502 ? "WARN" : status >= 500 ? "ERROR" : "INFO";
    String message =
        status == 502
            ? "外部データストアへのアクセスに失敗しました"
            : status >= 500 ? "リクエスト処理中に内部エラーが発生しました" : "リクエスト処理が完了しました";
    Object route = request.getAttribute(HandlerMapping.BEST_MATCHING_PATTERN_ATTRIBUTE);
    Object profile = request.getAttribute(PROFILE_ATTRIBUTE);
    String runId = request.getHeader(ApiController.BENCHMARK_RUN_ID_HEADER);

    Map<String, Object> line = new LinkedHashMap<>();
    line.put("level", level);
    line.put("message", message);
    line.put("implementation", "java");
    line.put("lambda_request_id", lambdaRequestId(request));
    line.put("benchmark_run_id", runId == null ? "" : runId);
    line.put("benchmark_request_index", requestIndex(request.getHeader(BENCHMARK_REQUEST_INDEX_HEADER)));
    line.put("route", route == null ? request.getRequestURI() : route.toString());
    line.put("profile", profile == null ? "unknown" : profile.toString());
    line.put("status_code", status);
    return line;
  }

  private static String lambdaRequestId(HttpServletRequest request) {
    // Lambda上ではアダプターがLambdaのContextをリクエスト属性に載せる。テストなどでは空にする。
    return request.getAttribute(RequestReader.LAMBDA_CONTEXT_PROPERTY) instanceof Context context
        ? context.getAwsRequestId()
        : "";
  }

  /** ランナーが付与した計測番号をログ相関用に正規化し、通常リクエストや不正値は0とする。 */
  static long requestIndex(String value) {
    if (value == null || !value.matches("[+-]?\\d+")) {
      return 0;
    }
    try {
      return Long.parseLong(value);
    } catch (NumberFormatException overflow) {
      return 0;
    }
  }
}
