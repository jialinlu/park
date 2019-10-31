import psycopg2 as pg
import getpass
from sql_rep.utils import *

import pdb

PG_HINT_CMNT_TMP = '''/*+
{COMMENT}
*/
'''
PG_HINT_JOIN_TMP = "{JOIN_TYPE} ({TABLES})"
PG_HINT_CARD_TMP = "Rows ({TABLES} #{CARD})"
PG_HINT_JOINS = {}
PG_HINT_JOINS["Nested Loop"] = "NestLoop"
PG_HINT_JOINS["Hash Join"] = "HashJoin"
PG_HINT_JOINS["Merge Join"] = "MergeJoin"


def _get_cost(sql, con):
    assert "explain" in sql
    cur = con.cursor()
    cur.execute(sql)
    explain = cur.fetchone()[0][0]
    all_costs = extract_values(explain, "Total Cost")
    cost = max(all_costs)
    cur.close()
    return cost

def _gen_pg_hint_cards(cards):
    '''
    '''
    card_str = ""
    for aliases, card in cards.items():
        card_line = PG_HINT_CARD_TMP.format(TABLES = aliases,
                                            CARD = card)
        card_str += card_line + "\n"
    return card_str

def _gen_pg_hint_join(join_ops):
    '''
    '''
    join_str = ""
    for tables, join_op in join_ops.items():
        join_line = PG_HINT_JOIN_TMP.format(TABLES = tables,
                                            JOIN_TYPE = PG_HINT_JOINS[join_op])
        join_str += join_line + "\n"
    return join_str

def get_pg_join_order(sql, join_graph, con):
    '''
    Ryan's implementation.
    '''
    physical_join_ops = {}
    def __extract_jo(plan):
        if plan["Node Type"] in join_types:
            left = list(extract_aliases(plan["Plans"][0], jg=join_graph))
            right = list(extract_aliases(plan["Plans"][1], jg=join_graph))
            all_froms = left + right
            all_nodes = []
            for from_clause in all_froms:
                from_alias = from_clause[from_clause.find("as ")+3:]
                if "_info" in from_alias:
                    print(from_alias)
                    pdb.set_trace()
                all_nodes.append(from_alias)
            all_nodes.sort()
            all_nodes = " ".join(all_nodes)
            physical_join_ops[all_nodes] = plan["Node Type"]

            if len(left) == 1 and len(right) == 1:
                return left[0] +  " CROSS JOIN " + right[0]

            if len(left) == 1:
                return left[0] + " CROSS JOIN (" + __extract_jo(plan["Plans"][1]) + ")"

            if len(right) == 1:
                return "(" + __extract_jo(plan["Plans"][0]) + ") CROSS JOIN " + right[0]

            return ("(" + __extract_jo(plan["Plans"][0])
                    + ") CROSS JOIN ("
                    + __extract_jo(plan["Plans"][1]) + ")")

        return __extract_jo(plan["Plans"][0])

    cursor = con.cursor()
    cursor.execute(sql)
    exp_output = cursor.fetchall()
    cursor.close()

    return __extract_jo(exp_output[0][0][0]["Plan"]), physical_join_ops

def _get_modified_sql(sql, cardinalities, join_ops):
    '''
    @cardinalities: dict
    @join_ops: dict

    @ret: sql, augmented with appropriate comments.
    '''
    if "explain" not in sql:
        sql = "explain (format json) " + sql

    comment_str = ""
    if cardinalities is not None:
        card_str = _gen_pg_hint_cards(cardinalities)
        # gen appropriate sql with comments etc.
        comment_str += card_str

    if join_ops is not None:
        join_str = _gen_pg_hint_join(join_ops)
        comment_str += join_str

    pg_hint_str = PG_HINT_CMNT_TMP.format(COMMENT=comment_str)
    sql = pg_hint_str + "\n" + sql
    return sql

def compute_join_order_loss_pg_single(query, true_cardinalities,
        est_cardinalities):
    '''
    @query: str
    @true_cardinalities:
        key:
            sort([table_1 / alias_1, ..., table_n / alias_n])
        val:
            float
    @est_cardinalities:
        key:
            sort([table_1 / alias_1, ..., table_n / alias_n])
        val:
            float

    '''
    # set est cardinalities
    join_graph = extract_join_graph(query)
    os_user = getpass.getuser()
    if os_user == "ubuntu":
        con = pg.connect(port=5432,dbname="imdb",
                user=os_user,password="")
    else:
        con = pg.connect(host="localhost",port=5432,dbname="imdb",
                user="pari",password="")

    est_card_sql = _get_modified_sql(query, est_cardinalities, None)

    # find join order
    join_order_sql, join_ops = get_pg_join_order(est_card_sql, join_graph, con)
    est_opt_sql = nx_graph_to_query(join_graph, from_clause=join_order_sql)
    # add the join ops etc. information
    est_opt_sql = _get_modified_sql(est_opt_sql, true_cardinalities,
            join_ops)
    est_cost = _get_cost(est_opt_sql, con)
    # this would not use cross join syntax, so should work fine with
    # join_collapse_limit = 1 as well.
    opt_sql = _get_modified_sql(query, true_cardinalities, None)
    opt_cost = _get_cost(opt_sql, con)
    if est_cost < opt_cost:
        est_cost = opt_cost

    # print("est_cost: {}, opt_cost: {}, diff: {}".format(est_cost, opt_cost,
        # est_cost-opt_cost))
    con.close()
    return est_cost, opt_cost


