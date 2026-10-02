"""Provider-consent guardrail for independent market research.

User authorization is required before introducing or invoking a NEW broker or
trading API/provider in this research branch.

This guardrail was added after an unapproved Upstox verification path was
introduced during Cohort-4 data QA. That path has been removed.

For the current Cohort-4 QA recovery, Breeze is the only provider authorized
because it is the provider already used by the user's frozen collection flow.
No Upstox, Dhan, Kite, or other broker/trading API may be added or invoked for
this recovery unless the user explicitly authorizes that provider first.
"""

POLICY_VERSION = "EXTERNAL_TRADING_API_CONSENT_V1"

POLICY = {
    "new_broker_or_trading_api_requires_explicit_user_consent": True,
    "current_cohort4_qa_authorized_providers": ["BREEZE"],
    "current_cohort4_qa_unapproved_providers": [
        "UPSTOX",
        "DHAN",
        "KITE",
        "OTHER_BROKER_OR_TRADING_APIS",
    ],
    "no_implicit_provider_substitution": True,
    "no_account_signup_request_without_user_request": True,
    "research_only": True,
}
