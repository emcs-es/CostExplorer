import boto3
import csv
import os
from io import StringIO
from azure.storage.blob import BlobServiceClient
from datetime import datetime, timedelta


def get_cost_data(client, start_date, end_date, group_by):
    """
    Ejecuta una llamada a Cost Explorer con paginación
    """
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


def flatten_results(results, dimension_names, tipo_dimension):
    """
    Convierte la respuesta AWS a filas CSV
    """
    rows = []

    for day_result in results:
        fecha = day_result['TimePeriod']['Start']

        for group in day_result.get('Groups', []):
            keys = group.get('Keys', [])
            cost = group['Metrics']['UnblendedCost']['Amount']

            row = {
                'fecha': fecha,
                'tipo_dimension': tipo_dimension,
                'SERVICE': '',
                'LINKED_ACCOUNT': '',
                'REGION': '',
                'USAGE_TYPE': '',
                'INSTANCE_TYPE': '',
                'TAG': '',
                'coste': cost
            }

            for i, dim_name in enumerate(dimension_names):
                row[dim_name] = keys[i] if i < len(keys) else ''

            rows.append(row)

    return rows


def main(mytimer):

    # ==============================
    # Cliente AWS Cost Explorer
    # ==============================
    client = boto3.client(
        'ce',
        region_name='us-east-1',
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )

    # ==============================
    # Fechas (solo mes en curso e histórico 3 meses)
    # ==============================
    today = datetime.utcnow().date()

    month_start = today.replace(day=1)

    hist_end = month_start - timedelta(days=1)
    hist_start_month = today.month - 3
    hist_start_year = today.year

    if hist_start_month <= 0:
        hist_start_month += 12
        hist_start_year -= 1

    hist_start = hist_end.replace(
        year=hist_start_year,
        month=hist_start_month,
        day=1
    )

    periods = [
        (
            "MES_CURSO",
            month_start.strftime("%Y-%m-%d"),
            today.strftime("%Y-%m-%d")
        ),
        (
            "HISTORICO_3M",
            hist_start.strftime("%Y-%m-%d"),
            month_start.strftime("%Y-%m-%d")
        )
    ]

    # ==============================
    # Múltiples llamadas
    # ==============================
    query_sets = [
        (
            [
                {'Type': 'DIMENSION', 'Key': 'SERVICE'},
                {'Type': 'DIMENSION', 'Key': 'LINKED_ACCOUNT'}
            ],
            ['SERVICE', 'LINKED_ACCOUNT'],
            'SERVICE_ACCOUNT'
        ),
        (
            [
                {'Type': 'DIMENSION', 'Key': 'REGION'},
                {'Type': 'DIMENSION', 'Key': 'USAGE_TYPE'}
            ],
            ['REGION', 'USAGE_TYPE'],
            'REGION_USAGE'
        ),
        (
            [
                {'Type': 'DIMENSION', 'Key': 'INSTANCE_TYPE'},
                {'Type': 'TAG', 'Key': 'Customer'}
            ],
            ['INSTANCE_TYPE', 'TAG'],
            'INSTANCE_TAG'
        )
    ]

    # ==============================
    # Azure Blob
    # ==============================
    connection_string = os.environ["AzureWebJobsStorage"]

    blob_service_client = BlobServiceClient.from_connection_string(
        connection_string
    )

    container_name = "copydatacost"

    container_client = blob_service_client.get_container_client(
        container_name
    )

    # Borrar CSVs antiguos
    for blob in container_client.list_blobs():
        if blob.name.endswith(".csv"):
            container_client.delete_blob(blob.name)

    # ==============================
    # Crear y subir 1 CSV por periodo
    # ==============================
    for period_name, start_date, end_date in periods:

        all_rows = []

        for group_by, dimension_names, tipo_dimension in query_sets:

            results = get_cost_data(
                client,
                start_date,
                end_date,
                group_by
            )

            rows = flatten_results(
                results,
                dimension_names,
                tipo_dimension
            )

            for row in rows:
                row['periodo'] = period_name

            all_rows.extend(rows)

        output = StringIO()
        writer = csv.writer(output)

        writer.writerow([
            'periodo',
            'fecha',
            'tipo_dimension',
            'SERVICE',
            'LINKED_ACCOUNT',
            'REGION',
            'USAGE_TYPE',
            'INSTANCE_TYPE',
            'TAG',
            'coste'
        ])

        for row in all_rows:
            writer.writerow([
                row.get('periodo', ''),
                row.get('fecha', ''),
                row.get('tipo_dimension', ''),
                row.get('SERVICE', ''),
                row.get('LINKED_ACCOUNT', ''),
                row.get('REGION', ''),
                row.get('USAGE_TYPE', ''),
                row.get('INSTANCE_TYPE', ''),
                row.get('TAG', ''),
                row.get('coste', '')
            ])

        now_str = datetime.utcnow().strftime("%Y-%m-%d_%H-%M-%S")

        blob_name = (
            f"cost_{period_name.lower()}_{now_str}.csv"
        )

        blob_client = blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name
        )

        blob_client.upload_blob(
            output.getvalue(),
            overwrite=True
        )

        print(f"CSV subido a {container_name}/{blob_name}")