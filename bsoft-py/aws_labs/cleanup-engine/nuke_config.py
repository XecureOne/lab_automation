from __future__ import annotations

from pathlib import Path


MANAGEMENT_ACCOUNT_ID = "880690594512"
ROLE_NAME = "OrganizationAccountAccessRole"

PROTECTED_POLICY_NAMES = [
    "CoderCreatedIdentityBoundary",
    "DefaultIamPolicy",
]


def build_nuke_config(
    account_id: str,
    output_path: str | None = None,
) -> str:
    """
    Build an account-specific aws-nuke configuration.

    Returns the generated config file path.
    """

    account_id = str(account_id).strip()

    if not account_id.isdigit() or len(account_id) != 12:
        raise ValueError(
            f"Invalid AWS account ID: {account_id}"
        )

    if account_id == MANAGEMENT_ACCOUNT_ID:
        raise ValueError(
            "Refusing to generate aws-nuke config for management account"
        )

    if output_path is None:
        output_path = (
            f"logs/runtime/{account_id}/nuke-config.yaml"
        )

    path = Path(output_path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    protected_policy_arns = "\n".join(
        f'          - "arn:aws:iam::{account_id}:policy/{policy_name}"'
        for policy_name in PROTECTED_POLICY_NAMES
    )

    content = f"""regions:
  - ap-south-1
  - global

blocklist:
  - "{MANAGEMENT_ACCOUNT_ID}"

accounts:
  "{account_id}":
    filters:

      IAMRole:
        - "{ROLE_NAME}"

      IAMRolePolicyAttachment:
        - "{ROLE_NAME} -> AdministratorAccess"

      IAMRolePolicy:
        - property: role
          value: "{ROLE_NAME}"

      IAMPolicy:
{protected_policy_arns}
"""

    path.write_text(
        content,
        encoding="utf-8",
    )

    return str(path)