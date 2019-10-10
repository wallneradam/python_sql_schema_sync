"""
Module to compare two SQL files containing table schemas and generate queries to transform from the
source into the destination.

Notes:

It is not a full featured SQL parser, just a quick regular expresion based one. Because of this, you
cannot use 'CREATE TABLE', 'if not exists', etc. strings as field and table comments. It could be
solved by some text processing tricks, but not worth the effort. Just not use these SQL comments.

Also it cannot handle field and table renames. This means if you rename a field, it will create 2
commands: a DROP and a CREATE. Which cause all your data in the field will be lost. If it is a
problem, you may want to filter "drop" actions.
"""
import re
from enum import Enum
from typing import Dict, List, Tuple, Union
from collections import OrderedDict

__author__ = "Adam Wallner"
__credits__ = "Kirill Gerasimenko"

__license__ = "Apache-2.0"
__version__ = 0.1

__all__ = ['sync']


WHITESPACES = (' ', '\t', '\n', '\r')
OPERATORS = (',', '+', '-', '/', '*', '&', '<', '=', '>', '%', '^')


class _DataClass:
    def __repr__(self) -> str:
        return repr(self.__dict__)


class DiffInfo(_DataClass):
    src: str = ''
    dst: str = ''


class TableInfo(_DataClass):
    src_orphan: bool = False
    dst_orphan: str = False
    diffs: List[DiffInfo]

    def __init__(self) -> None:
        self.diffs = []


class ActionTypes(Enum):
    """ The possible action names """
    create = 'CREATE'  # CREATE TABLE action
    drop = 'DROP'  # DROP TABLE action
    add = 'ADD'  # ADD field action
    remove = 'REMOVE'  # DROP field action
    modify = 'MODIFY'  # MODIFY field action


def normalize_str(string: str) -> str:
    """
    Remove multiple spaces and make lowercase
    :param string: The string to normalize
    :return: The normalized string
    """
    string = string.lower()
    string = re.sub(r'\s+', ' ', string)
    return string


def normalize_expr(expression: str) -> str:
    """
    Normalze the given SQL expression to be able to compare
    :param expression: The expression to normalize
    :return: The normalized expression
    """
    res = ''

    in_string = False

    o = 0
    lc = None
    try:
        while True:
            c = expression[o]
            if not in_string:
                if c == "'" or c == '"':   # Found string start
                    in_string = c
                else:
                    # Ensure one space
                    if c in WHITESPACES:
                        if lc == ' ':
                            o += 1
                            continue
                        c = ' '
                    # Remove unnecessary spaces
                    if c == ' ' and lc in OPERATORS:
                        o += 1
                        continue
                    elif lc == ' ' and c in OPERATORS:
                        res = res[:-1]

                res += c.lower()

            else:
                if in_string and c == in_string:
                    in_string = False

                res += c

            lc = c
            o += 1

    except IndexError:  # It is expected: shows no more data
        pass

    return res


def get_delimiter_pos(sql: str, *, offset: int, delim: str = ';', skip_in_brackets=False) -> int:
    """
    Find next delimiter
    Supports one line (standard) and multiline SQL comments and strings
    :param sql: The schema SQL
    :param offset: The offster from we search delimiter
    :param delim: The delimiter we search for
    :param skip_in_brackets: If we don't want to check delimiter inside brackets
    :return: The offset of the next delimiter (;)
    """
    in_string = False
    in_brackets = 0

    try:
        while True:
            c = sql[offset]
            if not in_string:
                if c == delim and not in_brackets:  # Found the delimter!
                    return offset
                elif c == '"' or c == "'":  # Found string start
                    in_string = c

                elif skip_in_brackets:
                    if c == '(':
                        in_brackets += 1
                    elif c == ')':
                        in_brackets -= 1

            elif in_string and c == in_string:
                in_string = False

            offset += 1

    except IndexError:
        raise ValueError("Invalid SQL syntax, cannot find delimiter ('{delim}')!".format(delim=delim))


def filter_comments(sql: str) -> str:
    """
    Filter comments from SQL
    :param sql: The SQL needs to be filtered
    :return: Same SQL without comments
    """
    in_1l_comment = False
    in_ml_comment = False
    in_string = False
    o = 0
    res = ''
    try:
        while True:
            c = sql[o]
            if not in_1l_comment and not in_ml_comment and not in_string:
                if c == '-' and sql[o + 1] == '-':  # Standard SQL comment
                    in_1l_comment = True
                    o += 1
                elif c == '#':  # MySQL comment
                    in_1l_comment = True
                elif c == '/' and sql[o + 1] == '*':  # Multiline comment
                    in_ml_comment = True
                    o += 1
                elif c == '"' or c == "'":  # Found string start
                    in_string = c

            elif in_string and c == in_string:
                in_string = False

            elif in_1l_comment and c == '\n':
                in_1l_comment = False

            elif in_ml_comment and c == '*' and sql[o + 1] == '/':
                in_ml_comment = False
                o += 2
                continue

            if not in_1l_comment and not in_ml_comment:
                res += c

            o += 1

    except IndexError:  # It is expected: shows no more data
        pass

    return res


def get_table_names(sql: str) -> List[str]:
    """
    :param sql: The schema SQL
    :return: List of table names
    """
    m = re.findall(r"CREATE(?:\s*TEMPORARY)?\s*TABLE\s*(?:IF NOT EXISTS\s*)?(?:`?(?:\w+)`?\.)?`?(\w+)`?", sql, re.I)
    return m


def extract_table_sql(name: str, sql: str, *, remove_database_name_from_sql: bool = True) -> str:
    """
    Extract SQL for a specified table
    :param name: Name of the table
    :param sql: The schema SQL from we need to extract table
    :param remove_database_name_from_sql: If true, we filter out database names
    :return: The extracted table schema
    """
    result = None
    for m in re.compile(r"(CREATE(?:\s*TEMPORARY)?\s*TABLE\s*(?:IF NOT EXISTS\s*)?\s*)(?:`?(\w+)`?\.)?"
                        r"(?:`?({name})`?(?:\W|$))".format(name=name), re.I).finditer(sql):
        table_def = m.group()
        start = m.span(0)[0]
        offset = m.span(0)[1]
        database = m.groups()[1]
        end = get_delimiter_pos(sql, offset=offset)
        result = sql[start:end]
        if database and remove_database_name_from_sql:
            result = result.replace(table_def, m.groups()[0] + '`' + m.groups()[2] + '` ')
        result = result.strip()

    return result


def split_table_schema(table_name: str, sql: str, *, ignore_increment: bool = True) -> List[str]:
    """
    Splits table schema SQL into a list
    :param table_name: The name of the table
    :param sql: The table shchema SQL
    :param ignore_increment: If true, auto increment values are filtered
    :return: Splitted SQL
    """
    res = []
    bottom = []

    def process_line(line):
        """ Process line, make some default """
        nonlocal bottom

        # Add default sizes if not specified to make them comparable
        line = re.sub(r"(\sINT)\s(?=\w)", r"\1(11) ", line, flags=re.I)
        line = re.sub(r"(\sTINYINT)\s(?=\w)", r"\1(3) ", line, flags=re.I)
        line = re.sub(r"(\sSMALLINT)\s(?=\w)", r"\1(6) ", line, flags=re.I)
        line = re.sub(r"(\sBIGINT)\s(?=\w)", r"\1(20) ", line, flags=re.I)
        line = re.sub(r"(\sVARCHAR)\s(?=\w)", r"\1(255) ", line, flags=re.I)
        line = re.sub(r"(\sDATETIME)\s(?=\w)", r"\1(6) ", line, flags=re.I)
        # Bool is tinyint in MySQL
        line = re.sub(r"\sBOOL\s(?=\w)", r" TINYINT(1) ", line, flags=re.I)

        if ignore_increment:
            line = re.sub(r"\s+AUTO_INCREMENT=[0-9]+", '', line, flags=re.I)

        # PRIMARY and UNIQUE
        m = re.match(r"^(?!PRIMARY\s|UNIQUE\s|KEY\s)\s*(`?\w+`?).*?(PRIMARY|UNIQUE)(?: KEY)?", line, flags=re.I)
        if m:
            line = re.sub(r'\s*(?:PRIMARY KEY|UNIQUE)(?: KEY)?', '', line, flags=re.I)
            bottom.append("{type} KEY ".format(type=m.groups()[1]) +
                          "{key}".format(key=(m.groups()[0] + ' ') if m.groups()[1].upper() == 'UNIQUE' else '') +
                          "({field})".format(field=m.groups()[0]))

        else:
            # REFERENCES
            m = re.match(r"^(?!CONSTRAINT\s|FOREIGN\s*KEY\s)\s*`?(\w+)`?.*?(REFERENCES.*)", line, flags=re.I)
            if m:
                line = re.sub(r'\s*(?:REFERENCES).*$', '', line, flags=re.I)
                bottom.append("KEY `fk_{table_name}_{key}` (`{field}`)".format(table_name=table_name,
                                                                               key=m.groups()[0], field=m.groups()[0]))
                bottom.append("CONSTRAINT `fk_{table_name}_{key}` ".format(table_name=table_name,
                                                                           key=m.groups()[0]) +
                              "FOREIGN KEY (`{field}`) ".format(field=m.groups()[0]) +
                              "{references}".format(references=m.groups()[1]))
            else:
                # FOREIGN KEY without CONSTRAINT
                m = re.match(r"^FOREIGN\s*KEY\s*\(`?(\w+)`?.*?REFERENCES.*", line, flags=re.I)
                if m:
                    return [
                        "KEY `fk_{table_name}_{key}` (`{field}`)".format(table_name=table_name,
                                                                         key=m.groups()[0],
                                                                         field=m.groups()[0]),
                        "CONSTRAINT `fk_{table_name}_{key}` ".format(table_name=table_name,
                                                                     key=m.groups()[0]) + line
                    ]

        return [line]

    # Find opening bracket
    open_bracket_pos = get_delimiter_pos(sql, offset=0, delim='(')
    prefix = sql[:open_bracket_pos + 1]
    res.append(prefix)
    # The body (without prefix)
    body = sql[open_bracket_pos + 1:]
    # Split by commas
    p = 0
    try:
        while True:
            np = get_delimiter_pos(body, offset=p, delim=',', skip_in_brackets=True)
            res.extend(process_line(body[p:np].strip()))
            p = np + 1
    # ValueError is expected, when we have no more parts
    except ValueError:
        pass
    # We have a last part till the closing bracket
    close_bracket_pos = get_delimiter_pos(body, offset=p, delim=')', skip_in_brackets=True)
    res.extend(process_line(body[p:close_bracket_pos].strip()))

    # Add bottom
    res += bottom

    # Add suffix
    suffix = body[close_bracket_pos:].strip()
    if suffix:
        res.append(suffix)
    return res


def extract_and_normalize_keys(line: str) -> Tuple[str, str]:
    """
    Extract and normalize keys from a table schema "line"
    :param line: One line of table schema
    :return: A tuble containing the key and the line
    """
    k = ''
    # Key definition
    m = re.match(r"^(PRIMARY\s+KEY)|(((UNIQUE\s+)|(FULLTEXT\s+))?KEY\s+`?\w+`?)", line, flags=re.I)
    if m:
        k = m.group()

    else:
        # Foreign keys
        m = re.match(r"^(CONSTRAINT\s+`?\w+`?)", line, flags=re.I)
        if m:
            k = '!!' + m.group()  # '!!' is to make sure foreign keys will be synchronized before everything else

        else:
            # Value definition
            m = re.match(r"^`?\w+`?", line)
            if m:
                k = '!' + m.group()  # '!' is to make sure fields will be synchronised before the keys

    return normalize_str(k), line


def compare_table_sql(table_name: str, src_sql: str, dst_sql: str, *, ignore_increment: bool = True) -> List[DiffInfo]:
    """
    Create a list of differences between source and destination table schema
    :param table_name: The name of the table to compare
    :param src_sql: The source table schema SQL
    :param dst_sql: The destination table schema SQL
    :param ignore_increment: If true, auto increment values are filtered
    :return: The list off differences
    """
    res = []

    src_parts = split_table_schema(table_name, src_sql, ignore_increment=ignore_increment)
    dst_parts = split_table_schema(table_name, dst_sql, ignore_increment=ignore_increment)

    src_parts_dict = OrderedDict(
        [extract_and_normalize_keys(part) for part in src_parts[1:-1]])
    dst_parts_dict = OrderedDict(
        [extract_and_normalize_keys(part) for part in dst_parts[1:-1]])

    # Ensure we have indexes for foreign key constraints in destination table parts
    indexes_dict = {k: p for k, p in dst_parts_dict.items() if k.startswith('key')}
    for k, p in dict(dst_parts_dict).items():
        if k.startswith('!!'):
            k = k.replace('!!constraint', 'key')
            if k not in indexes_dict:
                dst_parts_dict[k] = re.sub(r'^CONSTRAINT\s+(`?\w+`?)\s+FOREIGN KEY\s+(\([^)]+\)).*$', r'KEY \1 \2', p,
                                           flags=re.I)

    src_keys = list(src_parts_dict.keys())
    dst_keys = list(dst_parts_dict.keys())

    # Fields first, then indexes - because fields are prefixed with '!'
    all_keys = sorted(set(src_keys + dst_keys))

    for key in all_keys:
        info = DiffInfo()
        in_src = key in src_keys
        in_dst = key in dst_keys
        src_orphan = in_src and not in_dst
        dst_orphan = in_dst and not in_src
        different = in_src and in_dst and normalize_expr(src_parts_dict[key]) != normalize_expr(dst_parts_dict[key])
        if src_orphan:
            info.src = src_parts_dict[key]
        elif dst_orphan:
            info.dst = dst_parts_dict[key]
        elif different:
            info.src = src_parts_dict[key]
            info.dst = dst_parts_dict[key]
        else:
            continue

        res.append(info)

    return res


def sanitize_sql(table_name: str, sql: str) -> str:
    """
    Make not so well formed SQL to more like standard
    :param table_name: The name of the table
    :param sql: The table schema SQL
    :return: The sanitized SQL
    """
    parts = split_table_schema(table_name, sql)
    return parts[0] + "\n    " + ",\n    ".join(parts[1:-1]) + "\n" + parts[-1]


def compare_tables(src: str, dst: str, *,
                   remove_database_name_from_sql: bool = True,
                   ignore_increment: bool = True,
                   ignore_if_not_exists: bool = False,
                   force_if_not_exists: bool = False,
                   ) -> Dict[str, TableInfo]:
    """
    Compare the given DB structures
    :param src: The SQL contains the source table schema definitions
    :param dst: The SQL contains the destination table schema definitons
    :param remove_database_name_from_sql: If True, we filter out database names
    :param ignore_increment: If true, auto increment values are filtered
    :param ignore_if_not_exists: Ignore all IF NOT EXISTS in src query
    :param force_if_not_exists: Force all commands to have IF NOT EXISTS
    :return: The differences by all the tables source and destination SQLs contain
    """
    src_table_names = get_table_names(src)
    dst_table_names = get_table_names(dst)

    common = [v for v in src_table_names if v in dst_table_names]
    src_orphans = [v for v in src_table_names if v not in common]
    dst_orphans = [v for v in dst_table_names if v not in common]
    all_tables = OrderedDict.fromkeys(src_table_names + dst_table_names)

    res = {}

    for table_name in all_tables:
        info = TableInfo()
        # Is it only in source
        if table_name in src_orphans:
            info.src_orphan = True
        # Is it only in destination
        elif table_name in dst_orphans:
            dst_sql = extract_table_sql(table_name, dst, remove_database_name_from_sql=remove_database_name_from_sql)
            # TODO: These may remove text from field and table comments, though very unlikely to put SQL there
            if ignore_increment:
                dst_sql = re.sub(r"\s*AUTO_INCREMENT=[0-9]+", '', dst_sql, flags=re.I)
            if ignore_if_not_exists:
                dst_sql = re.sub(r'IF NOT EXISTS\s*', '', dst_sql, flags=re.I)
            if force_if_not_exists:
                dst_sql = re.sub(r'(CREATE(?:\s*TEMPORARY)?\s*TABLE\s*)(?:IF\sNOT\sEXISTS\s*)?(`?\w+`?)',
                                 r'\1IF NOT EXISTS \2', dst_sql, flags=re.I)

            info.dst_orphan = sanitize_sql(table_name, dst_sql)

        else:
            src_sql = extract_table_sql(table_name, src, remove_database_name_from_sql=remove_database_name_from_sql)
            dst_sql = extract_table_sql(table_name, dst, remove_database_name_from_sql=remove_database_name_from_sql)
            diffs = compare_table_sql(table_name, src_sql, dst_sql, ignore_increment=ignore_increment)

            if diffs:
                info.diffs = diffs

        res[table_name] = info

    return res


def filter_diffs(compare_info: Dict[str, TableInfo], allowed_actions: Tuple[ActionTypes, ...]) -> Dict[str, TableInfo]:
    """
    Filter comparison results based on update type settings
    :param compare_info: The results of the compare_tables function
    :param allowed_actions: The tuple of enabled update types
    :return: The filtered info dict
    """
    res = {}
    for table, info in compare_info.items():
        if info.src_orphan and ActionTypes.drop in allowed_actions:
            res[table] = info
        elif info.dst_orphan and ActionTypes.create in allowed_actions:
            res[table] = info
        elif info.diffs:
            res_info = TableInfo()
            for field_info in info.diffs:
                if field_info.src and not field_info.dst:
                    if ActionTypes.remove in allowed_actions:
                        res_info.diffs.append(field_info)
                elif field_info.dst and not field_info.src:
                    if ActionTypes.add in allowed_actions:
                        res_info.diffs.append(field_info)
                elif ActionTypes.modify in allowed_actions:
                    res_info.diffs.append(field_info)
            if res_info.diffs:
                res[table] = res_info

    return res


def get_action_sql(table, sql, action) -> Tuple[str, int]:
    """
    Compile update SQL
    :param table: The name of the table
    :param sql: Field SQL
    :param action: The needed action
    :return: The update SQL according to the action
    """
    res = "ALTER TABLE `{table}` ".format(table=table)
    insert_direction = 0

    re_key_field = r"`?\w`?(?:\(\d+\))?"  # matches `name`(10)
    re_key_field_list = r"(?:{}(?:,\s?)?)+".format(re_key_field)  # matches `name`(10),`desc`(255)
    m = re.match(r"^((?:PRIMARY )|(?:UNIQUE )|(?:FULLTEXT ))?KEY `?(\w+)?`?\s(\({}\))".format(re_key_field_list), sql,
                 flags=re.I)
    if m:
        key_type = (m.groups()[0] or '').strip().upper()
        key_name = m.groups()[1].strip()
        fields = m.groups()[2].strip()

        if action == ActionTypes.drop:
            res += 'DROP PRIMARY KEY' if key_type == 'PRIMARY' else 'DROP INDEX `{}`'.format(key_name)
            insert_direction = -1

        elif action == ActionTypes.add:
            if key_type == 'PRIMARY':
                res += 'ADD PRIMARY KEY {fields}'.format(fields=fields)
            elif not key_type:
                res += 'ADD INDEX `{key_name}` {fields}'.format(key_name=key_name, fields=fields)
            else:
                res += 'ADD {key_type} `{key_name}` {fields}'.format(
                    key_type=key_type, key_name=key_name, fields=fields)  # //fulltext or unique
            insert_direction = 1

        elif action == ActionTypes.modify:
            if key_type == 'PRIMARY':
                res += 'DROP PRIMARY KEY, ADD PRIMARY KEY {fields}'.format(fields=fields)
            elif not key_type:
                res += 'DROP INDEX `{key_name}`, ADD INDEX ` {key_name}` {fields}'.format(
                    key_name=key_name, fields=fields)
            else:
                res += 'DROP INDEX `{key_name}`, ADD {key_type}` {key_name}` {fields}'.format(
                    key_type=key_type, key_name=key_name, fields=fields)
            insert_direction = -1

    # Foreign key drops should be before everything
    elif sql.startswith("CONSTRAINT ") and action == ActionTypes.drop:
        space_pos = sql.find(' ', 11)  # 11 is len("CONSTRAINT ")
        res += 'DROP ' + sql[:space_pos]
        insert_direction = -1

    # Other field operations
    else:
        res += action.value

        if action == ActionTypes.drop:
            space_pos = sql.find(' ')
            res += ' ' + sql[:space_pos]
            insert_direction = 1

        else:
            res += ' ' + sql
            # Modify constraint -> drop then create
            m = re.match(r'TABLE\s+`?([^` ]+)`?\s+MODIFY\s+CONSTRAINT `?([^` ]+)', res, flags=re.I)
            if m and m.groups()[0] and m.groups()[1]:
                table = m[0]
                foreign_key = m[1]
                res = re.sub('MODIFY', 'ADD', res, flags=re.I)
                res = "ALTER TABLE " + "`{table}` DROP FOREIGN key `{foreign_key}`; {res}".format(
                    table=table, foreign_key=foreign_key, res=res)

            elif sql.startswith("CONSTRAINT "):
                insert_direction = 1

    return res, insert_direction


def get_diff_sql(compare_info: Dict[str, TableInfo]) -> List[str]:
    """
    Generate the SQL queries from the results of compare_sql
    :param compare_info: The (filtered) results of the compare_tables function
    :return: The list of SQL queries needed to convert source into destination
    """
    sqls = []
    sqls_top = []
    sqls_bottom = []
    for table, info in compare_info.items():
        if info.src_orphan:
            # Drop table from source
            # noinspection SqlResolve
            sqls.append("DROP TABLE `{table}`".format(table=table))
        elif info.dst_orphan:
            # Create table in source
            sqls.append(info.dst_orphan)
        else:  # Field differences
            for field_info in info.diffs:
                if field_info.src and not field_info.dst:
                    sql = field_info.src
                    action = ActionTypes.drop
                elif field_info.dst and not field_info.src:
                    sql = field_info.dst
                    action = ActionTypes.add
                else:
                    sql = field_info.dst
                    action = ActionTypes.modify

                sql, insert_direction = get_action_sql(table, sql, action)

                if insert_direction == 0:
                    sqls.append(sql)
                elif insert_direction == -1:
                    sqls_top.append(sql)
                elif insert_direction == 1:
                    sqls_bottom.append(sql)

    return sqls_top + sqls + sqls_bottom


def sync(src: str, dst: str, *, as_str: bool = True,
         remove_database_name_from_sql: bool = True,
         ignore_increment: bool = True,
         ignore_if_not_exists: bool = True,
         force_if_not_exists: bool = True,
         allowed_actions: Tuple[ActionTypes, ...] = (
             ActionTypes.create,
             ActionTypes.drop,
             ActionTypes.add,
             ActionTypes.remove,
             ActionTypes.modify
         )) -> Union[List[str], str]:
    """
    Synchronize two database scheme
    Generates SQL queries to make 'src' schema the same as 'dst'
    :param src: The schema definition we need to transform from
    :param dst: The schema we need to transform to
    :param as_str: If True (default) we get result as a multiline SQL, otherwise a list of commands
    :param remove_database_name_from_sql: If database name matters, if False (default), it can be imported to other DB
    :param ignore_increment: If we want to ignore auto increment values (default is True)
    :param ignore_if_not_exists: Ignore all IF NOT EXISTS in src query
    :param force_if_not_exists: Force all commands to have IF NOT EXISTS
    :param allowed_actions: The update types we want to create queries for
    :return: List of queries or an SQL string containing commands to transform src into dest
    """
    res = []
    src = filter_comments(src)
    dst = filter_comments(dst)
    compare_info = compare_tables(src, dst,
                                  remove_database_name_from_sql=remove_database_name_from_sql,
                                  ignore_increment=ignore_increment,
                                  ignore_if_not_exists=ignore_if_not_exists,
                                  force_if_not_exists=force_if_not_exists)
    if compare_info:
        compare_info = filter_diffs(compare_info, allowed_actions=allowed_actions)
        if compare_info:
            res = get_diff_sql(compare_info)

    if as_str:
        return (";\n".join(res) + ";") if res else ''

    return res


########################################################################################################################

def _test1():
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
) CHARACTER SET utf8mb4;"""

    print(" - sql1 -> sql2:")
    print(sync(sql1, sql2))
    print("\n - sql2 -> sql1:")
    print(sync(sql2, sql1))
    print("\n - Filtered actions:")
    print(sync(sql1, sql2,
               allowed_actions=(ActionTypes.add, ActionTypes.create, ActionTypes.modify)))


def _test2():
    sql1 = """
CREATE TABLE `user` (
  `id` bigint(20) unsigned NOT NULL AUTO_INCREMENT,
  `full_name` varchar(255) NOT NULL,
  `email` varchar(255) NOT NULL,
  `created_at` datetime(6) NOT NULL,
  `modified_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`),
  KEY `user_full_na_117102_idx` (`full_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE `metadata` (
  `key` varchar(64) NOT NULL,
  `value` text NOT NULL,
  PRIMARY KEY (`key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""

    sql2 = """
CREATE TABLE `user` (
    `id` BIGINT UNSIGNED NOT NULL PRIMARY KEY AUTO_INCREMENT,
    `email` VARCHAR(128) NOT NULL UNIQUE,
    `full_name` VARCHAR(255) NOT NULL,
    `created_at` DATETIME(6) NOT NULL,
    `modified_at` DATETIME(6) NOT NULL,
    KEY `user_email_117101_idx` (`email`),
    KEY `user_full_na_117102_idx` (`full_name`)
) CHARACTER SET utf8mb4;
CREATE TABLE `metadata` (
    `key` VARCHAR(64) NOT NULL  PRIMARY KEY,
    `value` TEXT NOT NULL
) CHARACTER SET utf8mb4;
"""

    res = sync(sql1, sql2)
    print(res)


if __name__ == '__main__':
    print("* Test1")
    _test1()
    print("\n* Test2")
    _test2()
