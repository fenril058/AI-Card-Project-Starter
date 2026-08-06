SCHEMA_VERSION = "1.1.0"
USER_AGENT = "AI-Card-Project-LicenseManager/1.1"
CHUNK_SIZE = 8 * 1024 * 1024

VALID_REVIEW_STATUSES = {
    "pending",
    "approved",
    "rejected",
    "reviewing",
}

VALID_PERMISSION_STATUSES = {
    "allowed",
    "allowed_with_conditions",
    "prohibited",
    "not_required",
    "not_addressed",
    "unknown",
    "not_applicable",
}

PERMISSION_KEYS = (
    "generated_output_commercial_use",
    "generated_output_sale",
    "model_redistribution",
    "derivative_model_distribution",
    "hosted_inference_or_api",
    "attribution",
)
