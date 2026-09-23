"""Deterministic Russian evidence from observed metrics, without an LLM."""
from .roles import NodeFeatures, RoleAssignment


def evidence(node: NodeFeatures, assignment: RoleAssignment) -> str:
    descriptions = {
        "consolidator": f"Признаки консолидации: плательщиков {node.in_degree}, связей от seed {node.reachable_seeds}",
        "distributor": f"Признаки распределения: получателей {node.out_degree}, плательщиков {node.in_degree}",
        "transit": f"Гипотеза транзита: out/in={node.out_minor / node.in_minor:.3f}" if node.in_minor else "Гипотеза транзита",
        "terminal": f"Возможный конец цепочки: входящих операций {node.in_tx}, исходящих {node.out_tx}",
        "coordinator": f"Гипотеза координации: связей от seed {node.reachable_seeds}, других сообществ {node.other_neighbor_clusters}",
        "peripheral": f"Недостаточно признаков роли: входящих связей {node.in_degree}, исходящих {node.out_degree}",
    }
    # The most consequential warning is mandatory, never sliced mid-sentence.
    if node.depth_boundary:
        warning = ("Колено 4: исходящие видны не полностью." if node.out_tx
                   else "Колено 4: дальнейшие переводы не наблюдаются.")
    elif node.isolated:
        warning = "Связей в выгрузке нет; это не вывод о безопасности."
    elif node.is_seed:
        warning = "Вход seed неполон."
    elif assignment.role == "terminal":
        warning = "Только в выборке; удержание денег не доказано."
    elif assignment.role == "transit":
        warning = "Даты совместимы; движение тех же денег не доказано." if node.temporal_transit_confirmed else "Без подтверждения по датам."
    else:
        warning = "Гипотеза по неполной выборке."
    result = descriptions[assignment.role] + ". " + warning
    if len(result) > 200:
        raise ValueError("Evidence exceeds 200 characters; cannot silently drop its warning")
    return result
