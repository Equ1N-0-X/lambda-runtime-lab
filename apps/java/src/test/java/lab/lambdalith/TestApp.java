package lab.lambdalith;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.request;

import java.io.ByteArrayOutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/** Lambdaやサーバーを起動せずに、コントローラー・エラー変換・ログフィルターを組み立てる。 */
final class TestApp {

  private TestApp() {}

  static MockMvc mockMvc(ItemRepository repository) {
    return mockMvc(repository, new PrintStream(new ByteArrayOutputStream()));
  }

  static MockMvc mockMvc(ItemRepository repository, PrintStream logOutput) {
    return MockMvcBuilders.standaloneSetup(new ApiController(repository))
        .setControllerAdvice(new ApiExceptionHandler())
        .addFilters(new RequestLoggingFilter(logOutput))
        .build();
  }

  static MockHttpServletResponse send(
      MockMvc mvc, String method, String path, String body, Map<String, String> headers)
      throws Exception {
    return sendBytes(
        mvc, method, path, body == null ? null : body.getBytes(StandardCharsets.UTF_8), headers);
  }

  /** 本文をバイト列のまま送る。UTF-8として不正な本文など、文字列にできない入力の検証に使う。 */
  static MockHttpServletResponse sendBytes(
      MockMvc mvc, String method, String path, byte[] body, Map<String, String> headers)
      throws Exception {
    MockHttpServletRequestBuilder builder = request(HttpMethod.valueOf(method), path);
    headers.forEach(builder::header);
    if (body != null) {
      builder.contentType(MediaType.APPLICATION_JSON).content(body);
    }
    return mvc.perform(builder).andReturn().getResponse();
  }
}
