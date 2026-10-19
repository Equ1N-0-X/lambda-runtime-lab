package lab.lambdalith;

import java.util.Optional;

/** IDで一件を取得する保存先の境界。未登録は空、保存先障害は例外として返す。 */
public interface ItemRepository {
  Optional<Item> get(String id);
}
