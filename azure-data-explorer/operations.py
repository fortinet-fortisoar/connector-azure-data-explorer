"""
Copyright start
MIT License
Copyright (c) 2026 Fortinet Inc
Copyright end
"""

from connectors.core.connector import get_logger, ConnectorError
import requests
import json
from .microsoft_api_auth import *
from .constant import *

logger = get_logger('azure-data-explorer')


def kusto_request(method, endpoint, connector_info, config, json_body=None, params=None):
    try:
        ms = MicrosoftAuth(config)
        endpoint = ms.host + endpoint
        token = ms.validate_token(config, connector_info)
        headers = {
            'Authorization': token,
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        response = requests.request(method, endpoint, headers=headers, json=json_body, params=params,
                                    verify=ms.verify_ssl)
        if response.status_code in [200, 201, 202, 204]:
            if 'json' in str(response.headers.get('Content-Type', '')):
                return response.json()
            return dict()
        else:
            error_message = extract_kusto_error(response)
            raise ConnectorError(error_message)
    except requests.exceptions.SSLError:
        raise ConnectorError('SSL certificate validation failed')
    except requests.exceptions.ConnectTimeout:
        raise ConnectorError('The request timed out while trying to connect to the server')
    except requests.exceptions.ReadTimeout:
        raise ConnectorError('The server did not send any data in the allotted amount of time')
    except requests.exceptions.ConnectionError:
        raise ConnectorError('Unable to connect to the server. Please check the URL or your network connection.')
    except Exception as err:
        if isinstance(err, ConnectorError):
            raise err
        raise ConnectorError(str(err))


def check_payload(payload):
    updated_payload = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            nested = check_payload(value)
            if len(nested.keys()) > 0:
                updated_payload[key] = nested
        elif value:
            updated_payload[key] = value
    return updated_payload


def extract_kusto_error(response):
    """
    Extracts a human readable error message from a Kusto API error response.
    Kusto errors are often nested envelopes (error -> @message -> embedded JSON
    string with a deeper error), so this recursively drills down to the
    most specific (deepest) error message available.
    """

    def _extract_error(obj):
        if not isinstance(obj, dict):
            return None
        error = obj.get('error')
        if isinstance(error, dict):
            nested = _extract_error(error)
            if nested:
                return nested
        message = obj.get('@message') or obj.get('message')
        if isinstance(message, str) and message.strip():
            # nested kusto errors embed a JSON error object inside the message string
            idx = message.find('{')
            if idx != -1:
                try:
                    nested = _extract_error(json.loads(message[idx:]))
                    if nested:
                        return nested
                except Exception:
                    pass
            return message
        if isinstance(error, str) and error.strip():
            return error
        return None

    try:
        err_resp = response.json()
        result = _extract_error(err_resp)
        if result:
            return "{0}: {1}".format(response.status_code, result)
        return "{0}: {1}".format(response.status_code, response.text[:500])
    except Exception:
        return "{0}: {1}".format(response.status_code, response.text[:500])


def convert_query_response(query_response):
    """
    Converts a Kusto API response into a list of dictionaries for easy
    consumption in playbooks.

    Supports both response formats:
    - v2 query endpoint (/v2/rest/query): a JSON list of frames
      (DataSetHeader, DataTable, DataSetCompletion), where the data is in the
      DataTable frame with TableKind == "PrimaryResult".
    - v1 management endpoint (/v1/rest/mgmt): a JSON dict with a "Tables" key
      holding a list of tables.
    """
    result = []
    if not query_response:
        return result

    if isinstance(query_response, list):
        # v2 frame format: extract rows from the PrimaryResult DataTable frame
        for frame in query_response:
            if not isinstance(frame, dict):
                continue
            if frame.get('FrameType') == 'DataTable' and frame.get('TableKind') == 'PrimaryResult':
                columns = [col.get('ColumnName') for col in frame.get('Columns', [])]
                for row in frame.get('Rows', []):
                    if isinstance(row, dict):
                        result.append(row)
                    else:
                        result.append(dict(zip(columns, row)))
        return result

    # v1 dict format: {"Tables": [...]}
    tables = query_response.get('Tables') or query_response.get('tables') or []
    primary = None
    for table in tables:
        if isinstance(table, dict) and table.get('TableName') in ['PrimaryResult', '@PrimaryResult']:
            primary = table
            break
    if primary is None and tables and isinstance(tables[0], dict):
        primary = tables[0]
    if primary:
        columns = [col.get('ColumnName') for col in primary.get('Columns', [])]
        for row in primary.get('Rows', []):
            if isinstance(row, dict):
                result.append(row)
            else:
                result.append(dict(zip(columns, row)))
    return result


def get_query_payload(config, params):
    """
    Builds the Kusto query API request body (db, csl, properties).
    """
    csl = params.get('query')
    database = params.get('database')
    if not csl:
        raise ConnectorError("Query parameter is required")
    payload = {
        'db': database if database else "",
        'csl': csl,
        'properties': {
            'Options': {
                'request_timeout': params.get('query_timeout') or DEFAULT_QUERY_TIMEOUT
            }
        }
    }
    payload = check_payload(payload)
    return payload


def run_query(config, params, connector_info):
    """
    Executes a KQL query on the specified database and returns the result as a list of records.
    If the specified query is a control command (starts with a dot '.'), it is automatically
    routed to the management endpoint (/v1/rest/mgmt), as the v2 query endpoint does not
    support control commands.
    """
    payload = get_query_payload(config, params)
    csl = payload.get('csl', '')
    if csl.lstrip().startswith('.'):
        endpoint = MANAGEMENT_ENDPOINT
    else:
        endpoint = QUERY_ENDPOINT
    response = kusto_request("POST", endpoint, connector_info, config, json_body=payload)
    return {"data": convert_query_response(response)}


def run_management_command(config, params, connector_info):
    """
    Internal helper: executes a Kusto management (control) command such as .show tables
    on the specified database and returns the result as a list of records.
    Not exposed as a connector operation; use Run Query (run_query) with a dot
    command instead, which routes control commands here automatically.
    """
    payload = get_query_payload(config, params)
    response = kusto_request("POST", MANAGEMENT_ENDPOINT, connector_info, config, json_body=payload)
    return {"data": convert_query_response(response)}


def get_table_list(config, params, connector_info):
    """
    Lists all tables in the specified database using the .show tables control command.
    """
    params = dict(params)
    params['query'] = ".show tables"
    return run_management_command(config, params, connector_info)


def get_table_details(config, params, connector_info):
    """
    Retrieves details of a specific table, including its columns and data types,
    using the .show table <TableName> cslschema control command.
    """
    table_name = params.get('table')
    params = dict(params)
    params['query'] = ".show table {0} cslschema".format(table_name)
    return run_management_command(config, params, connector_info)


def get_table_data(config, params, connector_info):
    """
    Retrieves rows from a table using a KQL query with optional filtering, ordering,
    and record count limits.
    """
    table_name = params.get('table')
    query = table_name
    time_column = params.get('time_column')
    time_from = params.get('time_from')
    time_to = params.get('time_to')
    if time_column:
        conditions = []
        if time_from:
            conditions.append("{0} >= datetime({1})".format(time_column, time_from))
        if time_to:
            conditions.append("{0} <= datetime({1})".format(time_column, time_to))
        if conditions:
            query = "{0} | where {1}".format(query, ' and '.join(conditions))
    filter_query = params.get('filter')
    if filter_query:
        query = "{0} | {1}".format(query, filter_query)
    sort_order = params.get('order_by')
    if sort_order:
        query = "{0} | order by {1}".format(query, sort_order)
    limit = params.get('limit')
    if limit:
        query = "{0} | take {1}".format(query, int(limit))
    params = dict(params)
    params['query'] = query
    return run_query(config, params, connector_info)


def get_database_list(config, params, connector_info):
    """
    Lists the databases available on the configured Azure Data Explorer cluster using
    the .show databases control command. This is a cluster-scope command, so the
    placeholder database is used as the request context.
    """
    params = dict(params)
    params['query'] = ".show databases"
    return run_management_command(config, params, connector_info)


def ingest_inline_data(config, params, connector_info):
    """
    Writes (ingests) records into a table using the .ingest inline into control command.
    The CSV data provided is ingested into the specified table and database.
    """
    table_name = params.get('table')
    csv_data = params.get('csv_data')
    query = ".ingest inline into table {0} <| {1}".format(table_name, csv_data)
    payload = {
        'db': params.get('database'),
        'csl': query
    }
    response = kusto_request("POST", MANAGEMENT_ENDPOINT, connector_info, config, json_body=payload)
    return {"data": convert_query_response(response),
            "message": "Successfully ingested data into the table {0}".format(table_name)}


def delete_table_data(config, params, connector_info):
    """
    Deletes records from a table that match the specified KQL where-clause predicate
    using the .delete table <TableName> records control command.

    The .delete table records command requires the predicate to be a full tabular
    query referencing the table (e.g. "MyTable | where Col == 'v'"), so this
    operation builds it by prepending the table name to the where-clause provided
    by the user.
    """
    table_name = params.get('table')
    predicate = params.get('predicate')
    predicate = predicate.strip()
    query = ".delete table {0} records <| {0} | {1}".format(table_name, predicate)
    payload = {
        'db': params.get('database'),
        'csl': query
    }
    response = kusto_request("POST", MANAGEMENT_ENDPOINT, connector_info, config, json_body=payload)
    return {"data": convert_query_response(response),
            "message": "Successfully deleted records matching the given predicate from the table {0}".format(
                table_name)}


def delete_table(config, params, connector_info):
    """
    Deletes a table and its data from the database using the .drop table control command.
    """
    table_name = params.get('table')
    query = ".drop table {0}".format(table_name)
    payload = {
        'db': params.get('database'),
        'csl': query
    }
    response = kusto_request("POST", MANAGEMENT_ENDPOINT, connector_info, config, json_body=payload)
    return {"data": convert_query_response(response),
            "message": "Successfully deleted the table {0}".format(table_name)}


def _check_health(config, connector_info):
    try:
        if check(config, connector_info):
            return True
        else:
            raise ConnectorError("Invalid Credentials")
    except Exception as err:
        if isinstance(err, ConnectorError):
            raise err
        raise ConnectorError(str(err))


operations = {
    'run_query': run_query,
    'get_database_list': get_database_list,
    'get_table_list': get_table_list,
    'get_table_details': get_table_details,
    'get_table_data': get_table_data,
    'ingest_inline_data': ingest_inline_data,
    'delete_table_data': delete_table_data,
    'delete_table': delete_table
}
