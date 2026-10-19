package lab.lambdalith;

import com.amazonaws.serverless.exceptions.ContainerInitializationException;
import com.amazonaws.serverless.proxy.model.HttpApiV2ProxyRequest;
import com.amazonaws.serverless.proxy.model.AwsProxyResponse;
import com.amazonaws.serverless.proxy.spring.SpringBootLambdaContainerHandler;
import com.amazonaws.services.lambda.runtime.Context;
import com.amazonaws.services.lambda.runtime.RequestStreamHandler;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * API Gateway HTTP API（payload 2.0）のイベントをSpring MVCへ渡すLambdaのハンドラー。
 *
 * <p>Springの初期化はコールドスタート時に一度だけ行い、ウォームスタートでは同じアプリを再利用する。
 */
public class StreamLambdaHandler implements RequestStreamHandler {

  private static final SpringBootLambdaContainerHandler<HttpApiV2ProxyRequest, AwsProxyResponse>
      HANDLER;

  static {
    try {
      HANDLER = SpringBootLambdaContainerHandler.getHttpApiV2ProxyHandler(Application.class);
    } catch (ContainerInitializationException error) {
      // 初期化に失敗したLambdaはどの要求にも応えられないため、起動を失敗させる。
      throw new IllegalStateException("Spring Bootの初期化に失敗しました", error);
    }
  }

  @Override
  public void handleRequest(InputStream input, OutputStream output, Context context)
      throws IOException {
    HANDLER.proxyStream(input, output, context);
  }
}
