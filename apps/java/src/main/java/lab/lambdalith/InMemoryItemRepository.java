package lab.lambdalith;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.function.Function;
import java.util.stream.Collectors;

/** テストで外部I/Oなしに同じ取得契約を再現する保存先。 */
public final class InMemoryItemRepository implements ItemRepository {

  private final Map<String, Item> items;

  public InMemoryItemRepository(List<Item> items) {
    this.items =
        items.stream().collect(Collectors.toUnmodifiableMap(Item::id, Function.identity(), (a, b) -> a));
  }

  @Override
  public Optional<Item> get(String id) {
    return Optional.ofNullable(items.get(id));
  }
}
