from flask import Flask, render_template, request, redirect, url_for, session, send_file
import pandas as pd
import pytz
from filelock import FileLock
from datetime import datetime
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import base64
import os


app = Flask(__name__)

# =========================
# Files
# =========================
DATA_DIR = "/data"
os.makedirs(DATA_DIR, exist_ok=True)

file_path = 'input.xlsx'   # נשאר בתוך הפרויקט
file_path_output = os.path.join(DATA_DIR, 'output.xlsx')
output_lock_file_path = os.path.join(DATA_DIR, 'output.lock')

# חשוב: תחליפי למפתחות אמיתיים
encryption_key = b'Sixteen byte key'
app.secret_key = 'replace_with_real_secret_key'

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "replace_this_with_a_long_secret_token")


# =========================
# Admin helpers
# =========================
def is_admin_request():
    token = request.args.get("token", "")
    return token == ADMIN_TOKEN


# =========================
# Encryption helpers
# =========================
def pad(data: str) -> str:
    pad_len = 16 - len(data) % 16
    return data + chr(pad_len) * pad_len


def encrypt_string(plain_text: str, key: bytes) -> str:
    padded_text = pad(plain_text).encode('utf-8')
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded_text) + encryptor.finalize()
    return base64.b64encode(encrypted).decode('utf-8')


# =========================
# Input selection
# =========================
def pick_one_row_for_participant():
    """
    בוחר שורה אקראית מתוך input.xlsx בלי לחזור על RowID שכבר נבחר.
    מחזיר:
    - row_index: מספר שורה 1-based עבור השימוש באפליקציה
    - row_id: הערך הייחודי מתוך עמודת RowID
    """
    with FileLock(output_lock_file_path):
        df_input = pd.read_excel(file_path)

        if df_input.empty:
            raise ValueError("input.xlsx is empty")

        if 'RowID' not in df_input.columns:
            raise ValueError("input.xlsx must contain a unique 'RowID' column")

        try:
            df_output = pd.read_excel(file_path_output)
        except FileNotFoundError:
            df_output = pd.DataFrame()

        if 'RowID' in df_output.columns:
            used_row_ids = set(
                pd.to_numeric(df_output['RowID'], errors='coerce')
                .dropna()
                .astype(int)
                .tolist()
            )
        else:
            used_row_ids = set()

        available_df = df_input[~df_input['RowID'].isin(used_row_ids)]

        if available_df.empty:
            raise ValueError("No available rows left in input.xlsx")

        random_row = available_df.sample(n=1).iloc[0]
        row_index = int(random_row.name + 1)
        row_id = int(random_row['RowID'])

        return row_index, row_id


# =========================
# Output helpers
# =========================
def ensure_output_columns(df_output: pd.DataFrame) -> pd.DataFrame:
    needed_columns = [
        'signal',
        'TestDecision-החלטה של השלב אימון',
        'InitialDecision',
        'InitialConfidence',
        'FinalDecision',
        'DecisionExplanation',
        'UserID',
        'RowID',
        'GroupNum',
        'GeneratedPassword-סיסמה לתשלום',
        'ProlificCode',
    ]

    for col in needed_columns:
        if col not in df_output.columns:
            df_output[col] = ''

    text_columns = [
        'signal',
        'TestDecision-החלטה של השלב אימון',
        'InitialDecision',
        'InitialConfidence',
        'FinalDecision',
        'UserID',
        'GeneratedPassword-סיסמה לתשלום',
        'ProlificCode',
        'DecisionExplanation',
        'RowID',
        'GroupNum',
    ]

    for col in text_columns:
        df_output[col] = df_output[col].astype(str)

    return df_output


def save_results_to_output(
    row: int,
    row_id: int,
    signal: str,
    training_choice: str,
    initial_decision: str,
    initial_confidence: str,
    final_decision: str,
    group_num: int,
    decision_explanation: str
):
    """
    שומר את תוצאות הניסוי ל-output.xlsx
    """
    with FileLock(output_lock_file_path):
        try:
            df_output = pd.read_excel(file_path_output)
        except FileNotFoundError:
            df_output = pd.DataFrame()

        df_output = ensure_output_columns(df_output)
        next_empty_row = len(df_output)

        safe_signal = str(signal)[0].lower() if signal else 'x'
        safe_training = str(training_choice)[0].lower() if training_choice else 'x'
        safe_final = str(final_decision)[0].lower() if final_decision else 'x'
        password = f"{row_id}-{safe_signal}-{safe_training}-{safe_final}"
        encrypted_password = encrypt_string(password, encryption_key)

        participant_id = str(session.get('participant_id', 'Unknown'))
        input_user_id = str(session.get('input_user_id', ''))

        df_output.at[next_empty_row, 'signal'] = str(signal)
        df_output.at[next_empty_row, 'TestDecision-החלטה של השלב אימון'] = str(training_choice)
        df_output.at[next_empty_row, 'InitialDecision'] = str(initial_decision)
        df_output.at[next_empty_row, 'InitialConfidence'] = str(initial_confidence)
        df_output.at[next_empty_row, 'FinalDecision'] = str(final_decision)
        df_output.at[next_empty_row, 'UserID'] = str(input_user_id)
        df_output.at[next_empty_row, 'RowID'] = str(row_id)
        df_output.at[next_empty_row, 'GroupNum'] = str(group_num)
        df_output.at[next_empty_row, 'GeneratedPassword-סיסמה לתשלום'] = str(encrypted_password)
        df_output.at[next_empty_row, 'ProlificCode'] = str(participant_id)
        df_output.at[next_empty_row, 'DecisionExplanation'] = str(decision_explanation)

        df_output.to_excel(file_path_output, index=False)
        print(f"✅ Saved row {next_empty_row + 1} for participant {participant_id}")


# =========================
# Shared helper for phase pages
# =========================
def load_row_data(row: int):
    df = pd.read_excel(file_path)

    if row < 1 or row > len(df):
        return None, "Invalid row selected."

    data = df.loc[row - 1]

    position = int(data['Position'])
    already_invested = int(data['AlreadyInvested'])
    required_for_success = data['RequiredForSuccess']
    color = str(data['Color'])
    group_num = int(data['GroupNum'])
    user_id = str(data['UserID']) if 'UserID' in df.columns else ''
    invest_confidence = str(data['InvestConfidence']) if 'InvestConfidence' in df.columns else ''
    not_invest_confidence = str(data['NotInvestConfidence']) if 'NotInvestConfidence' in df.columns else ''

    investors_blue = int(already_invested)
    investors_not_blue = int(position - 1 - already_invested)

    has_threshold = pd.notna(required_for_success) and str(required_for_success).strip() not in ['', '-', 'nan', 'None']

    row_data = {
        'row': row,
        'signal': color,
        'color': color,
        'position': position,
        'investors_blue': investors_blue,
        'investors_not_blue': investors_not_blue,
        'investment_threshold': required_for_success if has_threshold else None,
        'invest_confidence': invest_confidence,
        'not_invest_confidence': not_invest_confidence,
        'group_num': group_num,
        'user_id': user_id
    }

    return row_data, None


# =========================
# Routes
# =========================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/consent')
def consent():
    return render_template('consent.html')


@app.route('/submit_consent', methods=['POST'])
def submit_consent():
    participant_id = request.form.get('participant_id', '').strip()

    if not participant_id:
        return "You must provide a valid participant ID."

    if request.form.get('consent') != 'agree':
        return redirect(url_for('no_consent'))

    session.clear()
    session['participant_id'] = participant_id

    try:
        selected_row, selected_row_id = pick_one_row_for_participant()
    except ValueError as e:
        return str(e)

    session['current_row'] = selected_row
    session['current_row_id'] = selected_row_id

    now = datetime.now(pytz.utc)
    session['time_start'] = now.strftime("%H:%M:%S")

    return redirect(url_for('training'))


@app.route('/game_rules')
def game_rules():
    return render_template('game_rules.html')


@app.route('/start_training')
def start_training():
    return redirect(url_for('training'))


@app.route('/training')
def training():
    return render_template('training.html')


@app.route('/check_training', methods=['POST'])
def check_training():
    """
    תואם ל-training.html:
    q1 = A
    q2 = Blue
    q3 = 5
    q4 = current month
    """
    q1 = request.form.get('q1')
    q2 = request.form.get('q2')
    q3 = request.form.get('q3')
    q4 = request.form.get('q4')

    valid_months = [
        datetime.now().strftime('%B').lower(),
        datetime.now().strftime('%b').lower()
    ]

    if (
        q1 != 'C' or
        q2 != 'Blue' or
        q3 != '5' or
        not q4 or
        q4.strip().lower() not in valid_months
    ):
        session.clear()
        return render_template('wrong_answers.html')

    session['training_choice'] = 'Continued'
    return redirect(url_for('game_rules'))


@app.route('/start_experiment')
def start_experiment():
    row = session.get('current_row')
    if not row:
        return redirect(url_for('index'))

    return redirect(url_for('phase1', row=row))


@app.route('/phase1/<int:row>')
def phase1(row):
    session_row = session.get('current_row')
    if not session_row or session_row != row:
        return redirect(url_for('index'))

    row_data, error = load_row_data(row)
    if error:
        return error

    session['signal'] = row_data['signal']
    session['current_group'] = row_data['group_num']
    session['input_user_id'] = row_data['user_id']

    return render_template(
        'experiment_phase1.html',
        row=row_data['row'],
        color=row_data['color'],
        position=row_data['position'],
        investors_blue=row_data['investors_blue'],
        investors_not_blue=row_data['investors_not_blue'],
        investment_threshold=row_data['investment_threshold']
    )


@app.route('/submit_phase1', methods=['POST'])
def submit_phase1():
    row_val = request.form.get('row')
    if not row_val:
        return "Row is missing"

    row = int(row_val)

    initial_decision = request.form.get('initial_decision', '')
    initial_confidence = request.form.get('initial_confidence', '')

    if not initial_decision:
        return "Initial decision is required."

    if not initial_confidence:
        return "Initial confidence is required."

    session['phase1_initial_decision'] = initial_decision
    session['phase1_initial_confidence'] = initial_confidence

    return redirect(url_for('phase2', row=row))


@app.route('/phase2/<int:row>')
def phase2(row):
    session_row = session.get('current_row')
    if not session_row or session_row != row:
        return redirect(url_for('index'))

    initial_decision = session.get('phase1_initial_decision')
    initial_confidence = session.get('phase1_initial_confidence')

    if not initial_decision or not initial_confidence:
        return redirect(url_for('phase1', row=row))

    row_data, error = load_row_data(row)
    if error:
        return error

    return render_template(
        'experiment_phase2.html',
        row=row_data['row'],
        color=row_data['color'],
        position=row_data['position'],
        investors_blue=row_data['investors_blue'],
        investors_not_blue=row_data['investors_not_blue'],
        investment_threshold=row_data['investment_threshold'],
        initial_decision=initial_decision,
        initial_confidence=initial_confidence,
        invest_confidence=row_data['invest_confidence'],
        not_invest_confidence=row_data['not_invest_confidence']
    )


@app.route('/submit_social_learning', methods=['POST'])
def submit_social_learning():
    row_val = request.form.get('row')
    if not row_val:
        return "Row is missing"

    row = int(row_val)
    row_id = session.get('current_row_id')
    training_choice = session.get('training_choice', '')
    signal = session.get('signal', '')
    group_num = session.get('current_group', 1)

    initial_decision = session.get('phase1_initial_decision', '')
    initial_confidence = session.get('phase1_initial_confidence', '')
    final_decision = request.form.get('decision', '')
    decision_explanation = request.form.get('reasoning', '')

    if not initial_decision:
        return "Initial decision is missing from session."

    if not initial_confidence:
        return "Initial confidence is missing from session."

    if not final_decision:
        return "Final decision is required."

    if row_id is None:
        return "RowID is missing from session"

    save_results_to_output(
        row=row,
        row_id=row_id,
        signal=signal,
        training_choice=training_choice,
        initial_decision=initial_decision,
        initial_confidence=initial_confidence,
        final_decision=final_decision,
        group_num=group_num,
        decision_explanation=decision_explanation
    )

    session['experiment_completed'] = True
    return redirect(url_for('final'))


@app.route('/no_consent')
def no_consent():
    return render_template('no_consent.html')


@app.route('/final')
def final():
    participant_id = str(session.get('participant_id', 'Unknown'))

    try:
        df_output = pd.read_excel(file_path_output)
        df_output = ensure_output_columns(df_output)
        df_output['ProlificCode'] = df_output['ProlificCode'].fillna('').astype(str)

        participant_rows = df_output[df_output['ProlificCode'] == participant_id]

        if len(participant_rows) > 0:
            encrypted_password = participant_rows.iloc[-1]['GeneratedPassword-סיסמה לתשלום']
        else:
            encrypted_password = "No password found"

    except FileNotFoundError:
        encrypted_password = "No password found"

    return render_template('final.html', password=encrypted_password)


# =========================
# Admin routes
# =========================
@app.route('/admin-download')
def admin_download():
    if not is_admin_request():
        return "Unauthorized", 403

    if not os.path.exists(file_path_output):
        return "No output file found.", 404

    return send_file(file_path_output, as_attachment=True)


@app.route('/admin-clear')
def admin_clear():
    if not is_admin_request():
        return "Unauthorized", 403

    with FileLock(output_lock_file_path):
        empty_df = pd.DataFrame()
        empty_df = ensure_output_columns(empty_df)
        empty_df.to_excel(file_path_output, index=False)

    return "Output file cleared successfully."


@app.route('/admin-count')
def admin_count():
    if not is_admin_request():
        return "Unauthorized", 403

    if not os.path.exists(file_path_output):
        return "0 rows saved"

    df_output = pd.read_excel(file_path_output)
    return f"{len(df_output)} rows saved"


# =========================
# Error handlers
# =========================
@app.errorhandler(500)
def internal_error(error):
    print(f"❌ 500 error: {error}")
    return render_template('error_page.html'), 500


@app.errorhandler(404)
def not_found(error):
    print(f"⚠️ 404 error: {error}")
    return redirect(url_for('index'))


@app.errorhandler(Exception)
def handle_exception(error):
    print(f"❌ General error: {error}")
    return render_template('error_page.html'), 500


# =========================
# Run app
# =========================
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
