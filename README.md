# Python SQL Schema Sync
Generates SQL queries to make source table schema the same as destination table.

Now it is only understand MySQL/MariaDB dialect. But it should not be too hard to implement for
other DBs like Postgres or SQLite.

**Important!!**
It cannot handle table or field renames, these will create a DROP and CREATE/ADD queries. So after renames data loss will occur.

## Features

- "AUTO INCREMENT" values can be ommitted by parameter
- "IF NOT EXISTS" can be removed or forced to be added by parameters
- Fields updates are come befero keys, to be sure fields are specified
- Actions (CREATE, DROP, ADD, MODIFY, REMOVE) can be filtered. This is good for preventing data loss
when fields/tables are renamed
- The result can be a string of sequential SQL or a list of SQL queries

### Not implemented

- Field orders are not taken into account (anyway storage order does not matter, I think)
- It cannot detect field/table renames. I don't know a sure way to detect it.

# How to use

```python
import schema_sync

sql1 = """CREATE TABLE `test` (
  `id` int(11) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;
CREATE TABLE `user` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `full_name` varchar(255) NOT NULL,
  `email` varchar(255) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `modified` DATETIME(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

sql2 = """CREATE TABLE `user` (
    `id` BIGINT UNSIGNED NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `full_name` VARCHAR(255) NOT NULL,
    `email` VARCHAR(255) NOT NULL UNIQUE,
    `test` BOOL NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    `modified_at` DATETIME(6) NOT NULL
) CHARACTER SET utf8mb4;
"""

print(schema_sync.sync(sql1, sql2))
# DROP TABLE `test`;
# ALTER TABLE `user` ADD `modified_at` DATETIME(6) NOT NULL;
# ALTER TABLE `user` ADD `test` BOOL NOT NULL;
# ALTER TABLE `user` DROP `modified`;

print(schema_sync.sync(sql2, sql1))
# ALTER TABLE `user` ADD `modified` DATETIME(6) NOT NULL;
# CREATE TABLE IF NOT EXISTS `test` (
#   `id` int(11) NOT NULL,
#   PRIMARY KEY (`id`)
# ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
# ALTER TABLE `user` DROP `modified_at`;
# ALTER TABLE `user` DROP `test`;

print(schema_sync.sync(sql1, sql2,
                       allowed_actions=(ActionTypes.add,
                                        ActionTypes.create,
                                        ActionTypes.modify)))
# ALTER TABLE `user` ADD `modified_at` DATETIME(6) NOT NULL;
# ALTER TABLE `user` ADD `test` BOOL NOT NULL;


```


