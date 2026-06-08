import boto3
import csv
import os
from io import StringIO
from azure.storage.blob import BlobServiceClient
from datetime import datetime, timedelta



def get_account_map():
    """
    Obtiene mapa id_cuenta -> nombre cuenta desde AWS Organizations
    """
    org = boto3.client(
        'organizations',
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )

    accounts = {}
    paginator = org.get_paginator('list_accounts')

    for page in paginator.paginate():
        for acc in page['Accounts']:
            accounts[acc['Id']] = acc['Name']

    return accounts


def get_cost_data(client, start_date, end_date, group_by):
    all_results = []
    next_token = None

    while True:
        params = {
            'TimePeriod': {
                'Start': start_date,
                'End': end_date
            },
            'Granularity': 'DAILY',
            'Metrics': ['UnblendedCost'],
            'GroupBy': group_by
        }

        if next_token:
            params['NextPageToken'] = next_token

        response = client.get_cost_and_usage(**params)

        all_results.extend(response.get('ResultsByTime', []))

        next_token = response.get('NextPageToken')
        if not next_token:
            break

    return all_results


def flatten_results(results, dimension_names, vista_coste, account_map):
    rows = []

    for day_result in results:
        fecha = day_result['TimePeriod']['Start']

        for group in day_result.get('Groups', []):
            keys = group.get('Keys', [])
            cost = round(float(group['Metrics']['UnblendedCost']['Amount']), 2)

            row = {
                'fecha': fecha,
                'vista_coste': vista_coste,
                'SERVICE': '',
                'id_cuenta': '',
                'cuenta': '',
                'REGION': '',
                'concepto_facturado': '',
                'servidor_utilizado': '',
                'cliente_asociado': '',
                'coste': cost
            }

            for i, dim_name in enumerate(dimension_names):
                if dim_name == 'LINKED_ACCOUNT':
                    acc_id = keys[i] if i < len(keys) else ''
                    row['id_cuenta'] = acc_id
                    row['cuenta'] = account_map.get(acc_id, '')
                elif dim_name == 'USAGE_TYPE':
                    row['concepto_facturado'] = keys[i] if i < len(keys) else ''
                elif dim_name == 'INSTANCE_TYPE':
                    row['servidor_utilizado'] = keys[i] if i < len(keys) else ''
                elif dim_name == 'TAG':
                    row['cliente_asociado'] = keys[i] if i < len(keys) else ''
                else:
                    row[dim_name] = keys[i] if i < len(keys) else ''

            rows.append(row)

    return rows


def main(mytimer):

    client = boto3.client(
        'ce',
        region_name='us-east-1',
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )

    account_map = get_account_map()

    today = datetime.utcnow().date()

    # Día anterior
    yesterday = today - timedelta(days=1)    
    
    period_name = "AYER"

    start_date = yesterday.strftime("%Y-%m-%d")

    end_date = today.strftime("%Y-%m-%d")


    query_sets = [
        (
            [
                {'Type': 'DIMENSION', 'Key': 'SERVICE'},
                {'Type': 'DIMENSION', 'Key': 'LINKED_ACCOUNT'}
            ],
            ['SERVICE', 'LINKED_ACCOUNT'],
            'recurso_por_cliente'
        ),
        (
            [
                {'Type': 'DIMENSION', 'Key': 'REGION'},
                {'Type': 'DIMENSION', 'Key': 'USAGE_TYPE'}
            ],
            ['REGION', 'USAGE_TYPE'],
            'consumo_por_region'
        ),
        (
            [
                {'Type': 'DIMENSION', 'Key': 'INSTANCE_TYPE'},
                {'Type': 'TAG', 'Key': 'Customer'}
            ],
            ['INSTANCE_TYPE', 'TAG'],
            'servicio_por_cliente'
        )
    ]

    connection_string = os.environ["AzureWebJobsStorage"]

    blob_service_client = BlobServiceClient.from_connection_string(
        connection_string
    )

    container_name = "copydatacost"

    container_client = blob_service_client.get_container_client(container_name)

    for blob in container_client.list_blobs():
        if blob.name.endswith(".csv"):
            container_client.delete_blob(blob.name)

    

        all_rows = []

        for group_by, dimension_names, vista_coste in query_sets:

            results = get_cost_data(
                client,
                start_date,
                end_date,
                group_by
            )

            rows = flatten_results(
                results,
                dimension_names,
                vista_coste,
                account_map
            )

            all_rows.extend(rows)

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            'fecha',
            'vista_coste',
            'SERVICE',
            'id_cuenta',
            'cuenta',
            'REGION',
            'concepto_facturado',
            'servidor_utilizado',
            'cliente_asociado',
            'coste'
        ])

        for row in all_rows:
            writer.writerow([
                row.get('fecha', ''),
                row.get('vista_coste', ''),
                row.get('SERVICE', ''),
                row.get('id_cuenta', ''),
                row.get('cuenta', ''),
                row.get('REGION', ''),
                row.get('concepto_facturado', ''),
                row.get('servidor_utilizado', ''),
                row.get('cliente_asociado', ''),
                row.get('coste', '')
            ])

        now_str = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")

        # nombre de periodo
        blob_name = f"cost_{period_name.lower()}_{now_str}.csv"

        blob_client = blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name
        )

        blob_client.upload_blob(
            output.getvalue(),
            overwrite=True
        )

        print(f"CSV subido a {container_name}/{blob_name}")
