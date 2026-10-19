package lab.lambdalith;

/** DynamoDBで共有するID、集計値、固定長payload。 */
public record Item(String id, long value, String payload) {}
