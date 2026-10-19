package lab.lambdalith;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.servlet.NoHandlerFoundException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/** 契約上のエラーはその状態と本文で返し、想定外の例外は詳細を隠して500にする。 */
@RestControllerAdvice
public class ApiExceptionHandler {

  @ExceptionHandler(AppError.class)
  public ResponseEntity<Map<String, Object>> appError(AppError error) {
    return ResponseEntity.status(error.status()).body(error.body());
  }

  /** 登録済み経路と一致しない場合は、契約で定めた未検出エラーを返す。 */
  @ExceptionHandler({NoHandlerFoundException.class, NoResourceFoundException.class})
  public ResponseEntity<Map<String, Object>> routeNotFound() {
    return appError(AppError.routeNotFound());
  }

  @ExceptionHandler(Exception.class)
  public ResponseEntity<Map<String, Object>> unexpected(Exception error) {
    return appError(AppError.internal());
  }
}
