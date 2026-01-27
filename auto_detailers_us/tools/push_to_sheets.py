import argparse, os, json, pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    args = ap.parse_args()

    creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_JSON","credentials.json")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID","")
    worksheet = os.environ.get("GOOGLE_SHEETS_WORKSHEET","detailers")

    if not spreadsheet_id:
        raise SystemExit("GOOGLE_SHEETS_SPREADSHEET_ID env var is required for push.")

    scope = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = service_account.Credentials.from_service_account_file(creds_path, scopes=scope)

    service = build("sheets","v4", credentials=creds)
    sheet = service.spreadsheets()

    df = pd.read_csv(args.csv)
    values = [df.columns.tolist()] + df.fillna("").values.tolist()

    body = {"values": values}
    sheet.values().clear(spreadsheetId=spreadsheet_id, range=worksheet).execute()
    sheet.values().update(spreadsheetId=spreadsheet_id, range=worksheet, valueInputOption="RAW", body=body).execute()
    print(f"Pushed {len(df)} rows to {worksheet}")

if __name__ == "__main__":
    main()
