"""Module for managing cases and judgements."""

import sqlalchemy as sa
from sqlalchemy import (
    Engine,
    TextClause
)
from masterbase.models import (
    CaseAction,
    CaseActionType,
    CaseBody,
    Verdict
)

# Core actions

def create_case(engine: Engine, target_steam_id: str) -> CaseBody | None:
    """Generate a case for the given target steam ID if it doesn't already exist."""
    existing_case = get_case(engine, target_steam_id)
    if existing_case is not None:
        return None

    with engine.connect() as conn:
        conn.execute(
            insert_action_sql(target_steam_id, CaseAction(
                actiontype=CaseActionType.CREATE_CASE,
                parameters=None,
                timestamp="",
            ))
        )
        conn.commit()

    return get_case(engine, target_steam_id)

def publish_case(engine: Engine, target_steam_id: str) -> CaseBody | None:
    """Make the case publicly available."""
    existing_case = get_case(engine, target_steam_id)
    if existing_case is None:
        return None

    with engine.connect() as conn:
        conn.execute(
            insert_action_sql(target_steam_id, CaseAction(
                actiontype=CaseActionType.PUBLISH_CASE,
                parameters=None,
                timestamp="",
            ))
        )
        conn.commit()

    return get_case(engine, target_steam_id)

def withdraw_case(engine: Engine, target_steam_id: str):
    """Make the case no longer publicly available."""
    existing_case = get_case(engine, target_steam_id)
    if existing_case is None:
        return None

    with engine.connect() as conn:
        conn.execute(
            insert_action_sql(target_steam_id, CaseAction(
                actiontype=CaseActionType.WITHDRAW_CASE,
                parameters=None,
                timestamp="",
            ))
        )
        conn.commit()

    return get_case(engine, target_steam_id)

def set_judgement(
    target_steam_id: str,
    verdict: Verdict,
    reasoning: str,
):
    """Set the overall judgement for the case."""
    # TODO: implement


def set_review(
    target_steam_id: str,
    reviewer_steam_id: str,
    verdict: Verdict,
    reasoning: str,
    session_id: str | None = None,
):
    """Set an individual's review for the case."""
    # TODO: implement

# Helpers

def get_case(engine: Engine, target_steam_id: str) -> CaseBody | None:
    """Retrieve all case data for the given target steam ID."""
    sql = """
        SELECT * FROM cases
        WHERE
            target_steam_id = :target_steam_id;
    """
    params = {"target_steam_id": target_steam_id}

    with engine.connect() as conn:
        result = conn.execute(
            sa.text(sql),
            params,
        )

        rows = result.all()
        if not rows or rows.count() == 0:
            return None
        
        case_body = CaseBody(
            target_steam_id=rows[0]['target_steam_id'],
            actions=[
                {
                    "action": row['action'],
                    "parameters": row['parameters'],
                    "timestamp": row['created_at'].isoformat(),
                }
                for row in rows
            ]
        )

        # Sort actions by timestamp
        case_body.actions.sort(key=lambda action: action['timestamp'])

        return case_body


def generate_cases(engine: Engine, count: int):
    """Automatically select a target and open a new case for them."""
    potential_targets = select_targets_for_case()

    # TODO: placeholder
    target_steam_id = potential_targets[0]

    return create_case(engine, target_steam_id)

def select_targets_for_case() -> list[str]:
    """Returns a list of potential targets for a case."""
    # TODO: implement
    return []

def case_is_open(engine: Engine, target_steam_id: str) -> bool | None:
    """Check if a case is currently open for the given target steam ID."""
    case = get_case(engine, target_steam_id)
    if case is None:
        return None

    # Use latest publish/withdraw action to determine if case is open
    for action in sorted(case.actions, key=lambda a: a['timestamp'] , reverse=True):
        if action['action'] == CaseActionType.PUBLISH_CASE:
            return True
        elif action['action'] == CaseActionType.WITHDRAW_CASE:
            return False

    # Cases default to unpublished
    return False

def insert_action_sql(target_steam_id: str, action: CaseAction) -> TextClause:
    """Generate SQL to insert an action into the cases table."""
    sql = """
        INSERT INTO cases (target_steam_id, action, parameters)
        VALUES (:target_steam_id, :action, :parameters);
    """
    sql = sa.text(sql).bindparams(
        target_steam_id=target_steam_id,
        action=action.actiontype,
        parameters=action.parameters,
    )
    return sql