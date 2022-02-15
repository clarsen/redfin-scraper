import numpy as np
import os
import pandas as pd
import sqlite3
from datetime import date
from dotenv import load_dotenv
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pretty_html_table import build_table
from smtplib import SMTP


load_dotenv()

SQLITE_DB_PATH = os.getenv('SQLITE_DB_PATH')
EMAIL_ACCOUNT = os.getenv('EMAIL_ACCOUNT')
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
RECIPIENT_EMAIL = os.getenv('RECIPIENT_EMAIL')
DIR_PATH = os.path.dirname(os.path.realpath(__file__))
SQLITE_DB_FULL_PATH = f'{DIR_PATH}/{SQLITE_DB_PATH}'


def get_listing_data(today_filter=True):
    conn = sqlite3.connect(SQLITE_DB_FULL_PATH)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(listing_full_details)")
    table_info = cur.fetchall()

    date_filter_str = ''
    if today_filter:
        today = date.today().strftime('%Y/%m/%d')
        date_filter_str = f"AND DATE='{today}' "

    select_batch_query = f"SELECT * FROM listing_full_details WHERE STATUS='Active' {date_filter_str}AND NUMBER_ROOMS>=3 AND NUMBER_BATHROOMS>=1.5"
    cur.execute(select_batch_query)
    query_results = cur.fetchall()

    if not query_results:
        return None

    column_names = [info[1] for info in table_info]
    df = pd.DataFrame(query_results, columns=column_names)

    standardizaiton_columns = ['PRICE', 'MORTGAGE',
                               'YEAR', 'SQFT', 'LOT_SIZE', 'SQFT_PRICE']

    lot_size_weight = 0.8
    for c in standardizaiton_columns:
        col_range = df[c].max() - df[c].min()
        col_min = df[c].min()
        if c == 'PRICE' or c == 'MORTGAGE':
            df['STD_' + c] = df.apply(lambda row: 1 -
                                      ((row[c] - col_min) / col_range), axis=1)
        elif c == 'LOT_SIZE':
            df['STD_' + c] = df.apply(lambda row: (
                (row[c] - col_min) / col_range) * lot_size_weight, axis=1)
        elif c == 'SQFT_PRICE':
            new_col = 'SQFT_PER_THOUSAND'
            df[new_col] = df.apply(
                lambda row: row['SQFT'] / (row['PRICE'] / 1000), axis=1)
            col_range = df[new_col].max() - df[new_col].min()
            col_min = df[new_col].min()
            df['STD_' + new_col] = df.apply(
                lambda row: (row[new_col] - col_min) / col_range, axis=1)
        else:
            df['STD_' +
                c] = df.apply(lambda row: (row[c] - col_min) / col_range, axis=1)

    df[['STD_PRICE',
        'STD_MORTGAGE',
        'STD_YEAR',
        'STD_SQFT',
        'STD_LOT_SIZE',
        'STD_SQFT_PER_THOUSAND']].describe()

    df['SCORE'] = df.apply(lambda row: np.sum([row['STD_PRICE'],
                                               row['STD_MORTGAGE'],
                                               row['STD_YEAR'],
                                               row['STD_SQFT'],
                                               row['STD_LOT_SIZE'],
                                               row['STD_SQFT_PER_THOUSAND']]), axis=1)
    df_sorted = df.sort_values(by=['SCORE'], ascending=False)
    # Define the columns that will appear in the report
    report_columns = ['URL', 'PRICE', 'MORTGAGE', 'YEAR', 'NUMBER_ROOMS',
                      'NUMBER_BATHROOMS', 'SQFT', 'LOT_SIZE', 'SQFT_PRICE', 'TIME_ON_REDFIN', 'DATE', 'SCORE']
    # Return the top 20 rows of the sorted df for the report
    return df_sorted[report_columns].iloc[:20]


def send_mail(city, body):
    message = MIMEMultipart()
    message['Subject'] = f'House Hunt Report'
    message['From'] = EMAIL_ACCOUNT
    message['To'] = RECIPIENT_EMAIL

    head_html = f"""\
    <html>
        <body>
            <h3>{city} Report</h3>
        </body>
    </html>
    """
    tail_html = """\
    <html>
        <body>
            <br>
            <br>
        </body>
    </html>
    """
    body_content = body

    message.attach(MIMEText(head_html, 'html'))
    message.attach(MIMEText(body_content, "html"))
    message.attach(MIMEText(tail_html, 'html'))
    msg_body = message.as_string()

    server = SMTP('smtp.gmail.com', 587)
    server.starttls()
    server.login(message['From'], EMAIL_PASSWORD)
    server.sendmail(message['From'], message['To'], msg_body)
    server.quit()


def generate_listing_report():
    listing_data = get_listing_data(today_filter=True)
    print('report generated')
    html_table = build_table(listing_data, 'blue_light')
    send_mail('Vancouver', html_table)
    print('report distributed')


generate_listing_report()
