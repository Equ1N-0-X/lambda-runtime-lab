package lab.lambdalith;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.services.dynamodb.DynamoDbClient;

/** Java Lambdaの起動処理として、AWSクライアントとアプリの部品を組み立てる。 */
@SpringBootApplication
public class Application {

  @Bean
  ItemRepository itemRepository() {
    String tableName = System.getenv("DYNAMODB_TABLE_NAME");
    // テーブル名が未設定なら保存先を決められないため、Lambdaの初期化を失敗させる。
    if (tableName == null || tableName.isBlank()) {
      throw new IllegalStateException("DYNAMODB_TABLE_NAMEの設定が必要です");
    }
    // リージョン・認証情報・接続先はSDK標準の環境変数から読む（AWS_ENDPOINT_URL_DYNAMODB を含む）。
    DynamoDbClient client =
        DynamoDbClient.builder().httpClientBuilder(UrlConnectionHttpClient.builder()).build();
    return new DynamoDbItemRepository(client, tableName);
  }

  @Bean
  FilterRegistrationBean<RequestLoggingFilter> requestLoggingFilter() {
    return new FilterRegistrationBean<>(new RequestLoggingFilter());
  }

  public static void main(String[] args) {
    SpringApplication.run(Application.class, args);
  }
}
