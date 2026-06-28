"""תחרויות ואירועים — קריאה מגיליון Google Sheets."""

import os, pickle, base64
import googleapiclient.discovery

SPREADSHEET_ID = '1SaUURPE3a2GgmYRtCTcr7zSUr_EbjeBFEYkk2Nwilow'


def _get_service():
    b64 = os.environ.get('GOOGLE_CREDS_B64')
    if b64:
        creds = pickle.loads(base64.b64decode(b64))
    else:
        with open(os.path.expanduser('~/.wolves_judo_token.pickle'), 'rb') as f:
            creds = pickle.load(f)
    return googleapiclient.discovery.build('sheets', 'v4', credentials=creds)


def get_competitions() -> list[dict]:
    """Return list of competition tabs with their participants."""
    service = _get_service()
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID, includeGridData=False
    ).execute()
    tabs = [s['properties']['title'] for s in meta['sheets'] if s['properties']['title'] != 'תחרות']
    result = []
    for tab in tabs:
        data = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID, range=f"'{tab}'!A1:H100"
        ).execute().get('values', [])
        participants = []
        for row in data[1:]:  # skip header
            if len(row) >= 3 and row[0].strip():
                name = f"{row[1]} {row[2]}".strip() if len(row) > 2 else row[1]
                club = row[3].strip() if len(row) > 3 else ''
                year = row[4].strip() if len(row) > 4 else ''
                medal = row[5].strip() if len(row) > 5 else ''
                participants.append({
                    'name': name, 'club': club, 'year': year, 'medal': medal
                })
        result.append({'competition': tab, 'participants': participants})
    return result


def get_stats() -> dict:
    """Return summary stats for all competitions."""
    comps = get_competitions()
    total_participants = sum(len(c['participants']) for c in comps)
    medals = {}
    by_competition = {}
    for c in comps:
        by_competition[c['competition']] = len(c['participants'])
        for p in c['participants']:
            if p['medal']:
                medals[p['medal']] = medals.get(p['medal'], 0) + 1
    return {
        'total_competitions': len(comps),
        'total_participants': total_participants,
        'by_competition': by_competition,
        'medals': medals,
        'competitions': comps,
    }
