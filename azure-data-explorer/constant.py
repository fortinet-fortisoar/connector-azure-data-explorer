"""
Copyright start
MIT License
Copyright (c) 2026 Fortinet Inc
Copyright end
"""

REFRESH_TOKEN_FLAG = False

AUTH_USING_APP = "Without a User - Application Permission"
AUTH_BEHALF_OF_USER = "On behalf of User - Delegate Permission"

# API Version (Azure Resource Manager)
API_VERSION = "2025-02-14"

# grant types
AUTHORIZATION_CODE = 'authorization_code'
REFRESH_TOKEN = 'refresh_token'

# endpoints
AUTH_URL = 'https://login.microsoftonline.com'

# Azure Data Explorer (Kusto) REST API endpoints
QUERY_ENDPOINT = "/v2/rest/query"
MANAGEMENT_ENDPOINT = "/v1/rest/mgmt"

# default KQL query timeout
DEFAULT_QUERY_TIMEOUT = "00:03:00"
