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
    MockHttpServletRequestBuilder builder = request(HttpMethod.valueOf(method), path);
    headers.forEach(builder::header);
    if (body != null) {
      builder.contentType(MediaType.APPLICATION_JSON).content(body.getBytes(StandardCharsets.UTF_8));
    }
    return mvc.perform(builder).andReturn().getResponse();
  }
}
