"""
Modern Money Tracker Application
A professional personal finance management tool with goal tracking and reporting
"""

import sys
import sqlite3
from datetime import datetime, date
from typing import Optional, List, Tuple, Dict, Any
from dataclasses import dataclass
from contextlib import contextmanager
import csv
import smtplib
from email.message import EmailMessage

import matplotlib.pyplot as plt

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QFrame, QDialog, QLineEdit,
    QDoubleSpinBox, QSpinBox, QDateEdit, QFormLayout, QProgressBar,
    QMessageBox, QScrollArea, QGroupBox, QCheckBox
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QFont


# ==================== Data Models ====================

@dataclass
class Transaction:
    """Represents a financial transaction"""
    id: Optional[int]
    amount: float
    date: str
    note: str


@dataclass
class Goal:
    """Represents a savings goal"""
    id: Optional[int]
    name: str
    target: float
    saved: float
    allocation: int
    deadline: str
    notified: bool = False
    
    @property
    def progress_percentage(self) -> int:
        """Calculate goal completion percentage"""
        if self.target <= 0:
            return 0
        return min(int((self.saved / self.target) * 100), 100)
    
    @property
    def days_remaining(self) -> int:
        """Calculate days until deadline"""
        try:
            deadline_date = date.fromisoformat(self.deadline)
            return (deadline_date - date.today()).days
        except ValueError:
            return 0


# ==================== Database Manager ====================

class DatabaseManager:
    """Manages all database operations with proper connection handling"""
    
    def __init__(self, db_path: str = "finance.db"):
        self.db_path = db_path
        self._initialize_database()
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _initialize_database(self):
        """Create database tables if they don't exist"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Transactions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    amount REAL NOT NULL,
                    date TEXT NOT NULL,
                    note TEXT
                )
            """)
            
            # Goals table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target REAL NOT NULL,
                    saved REAL DEFAULT 0,
                    allocation INTEGER NOT NULL,
                    deadline TEXT NOT NULL,
                    notified INTEGER DEFAULT 0
                )
            """)
            
            # Allowance table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS allowance (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    amount REAL DEFAULT 0,
                    last_applied TEXT
                )
            """)

            # App settings table (reporting and email configuration)
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    smtp_server TEXT,
                    smtp_port INTEGER,
                    use_ssl INTEGER,
                    sender_email TEXT,
                    sender_password TEXT,
                    recipient_email TEXT,
                    auto_email INTEGER,
                    last_email_sent TEXT
                )
                """
            )
            # Ensure defaults exist
            cursor.execute("SELECT COUNT(1) FROM app_settings WHERE id = 1")
            exists = cursor.fetchone()[0]
            if not exists:
                cursor.execute(
                    """
                    INSERT INTO app_settings (
                        id, smtp_server, smtp_port, use_ssl, sender_email,
                        sender_password, recipient_email, auto_email, last_email_sent
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "smtp.gmail.com", 465, 1, "", "", "",
                        0, None
                    )
                )
            
            # Migrate existing database
            self._migrate_database(cursor)
    
    def _migrate_database(self, cursor):
        """Migrate existing database to new schema"""
        # Check if goals table needs migration
        cursor.execute("PRAGMA table_info(goals)")
        columns = {row[1] for row in cursor.fetchall()}
        
        # If either 'deadline' or 'notified' is missing, rebuild the table and preserve data
        if ('deadline' not in columns) or ('notified' not in columns):
            # Backup existing rows with whatever columns are available
            existing_cols = [c for c in ['id', 'name', 'target', 'saved', 'allocation', 'deadline', 'notified'] if c in columns]
            select_sql = "SELECT " + ", ".join(existing_cols) + " FROM goals"
            cursor.execute(select_sql)
            old_goals = cursor.fetchall()
            
            # Drop old table
            cursor.execute("DROP TABLE goals")
            
            # Create new table with the latest schema
            cursor.execute("""
                CREATE TABLE goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    target REAL NOT NULL,
                    saved REAL DEFAULT 0,
                    allocation INTEGER NOT NULL,
                    deadline TEXT NOT NULL,
                    notified INTEGER DEFAULT 0
                )
            """)
            
            # Restore data with sensible defaults for missing columns
            default_deadline = date.today().replace(year=date.today().year + 1).isoformat()
            
            for row in old_goals:
                # sqlite3.Row supports dict-like access when row_factory is set
                keys = row.keys() if hasattr(row, "keys") else []
                get = (lambda k, default=None: row[k] if k in keys else default)
                
                values = (
                    get('id', None),
                    get('name', ''),
                    get('target', 0.0),
                    get('saved', 0.0),
                    get('allocation', 0),
                    get('deadline', default_deadline) or default_deadline,
                    get('notified', 0) or 0,
                )
                
                cursor.execute(
                    """
                    INSERT INTO goals (id, name, target, saved, allocation, deadline, notified)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values
                )

    # ========== Settings Operations ==========

    def get_settings(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM app_settings WHERE id = 1")
            row = cursor.fetchone()
            # Defaults if row missing
            defaults = {
                'smtp_server': 'smtp.gmail.com',
                'smtp_port': 465,
                'use_ssl': 1,
                'sender_email': 'myteam.noreply@gmail.com',
                'sender_password': 'quzq gevh ctws gtpp',
                'recipient_email': 'foxynoxy07@gmail.com',
                'auto_email': 0,
                'last_email_sent': None,
            }
            if not row:
                return defaults
            res = {k: row[k] if k in row.keys() else defaults[k] for k in defaults.keys()}
            return res

    def save_settings(self, settings: Dict[str, Any]) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE app_settings
                SET smtp_server = ?, smtp_port = ?, use_ssl = ?, sender_email = ?,
                    sender_password = ?, recipient_email = ?, auto_email = ?
                WHERE id = 1
                """,
                (
                    settings.get('smtp_server', 'smtp.gmail.com'),
                    int(settings.get('smtp_port', 465)),
                    1 if settings.get('use_ssl', 1) else 0,
                    settings.get('sender_email', ''),
                    settings.get('sender_password', ''),
                    settings.get('recipient_email', ''),
                    1 if settings.get('auto_email', 0) else 0,
                ),
            )

    def get_last_email_sent(self) -> Optional[str]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_email_sent FROM app_settings WHERE id = 1")
            row = cursor.fetchone()
            return row[0] if row and row[0] else None

    def update_email_sent(self, month_str: str) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE app_settings SET last_email_sent = ? WHERE id = 1",
                (month_str,),
            )
    
    # ========== Transaction Operations ==========
    
    def add_transaction(self, amount: float, note: str) -> None:
        """Add a new transaction"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO transactions (amount, date, note) VALUES (?, ?, ?)",
                (amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), note)
            )
    
    def get_balance(self) -> float:
        """Get current balance"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions")
            return cursor.fetchone()[0]
    
    def get_monthly_transactions(self, year: int, month: int) -> List[Tuple[float, str]]:
        """Get all transactions for a specific month"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT amount, date
                FROM transactions
                WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ?
                ORDER BY date
            """, (str(year), f"{month:02d}"))
            return cursor.fetchall()
    
    def get_monthly_summary(self, year: int, month: int) -> Tuple[float, float]:
        """Get income and expense summary for a month"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) as income,
                    COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) as expense
                FROM transactions
                WHERE strftime('%Y', date) = ? AND strftime('%m', date) = ?
            """, (str(year), f"{month:02d}"))
            row = cursor.fetchone()
            return row[0], row[1]
    
    # ========== Goal Operations ==========
    
    def add_goal(self, goal: Goal) -> int:
        """Add a new savings goal and return its ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO goals (name, target, saved, allocation, deadline, notified)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (goal.name, goal.target, goal.saved, goal.allocation, goal.deadline, 0),
            )
            return int(cursor.lastrowid or 0)
    
    def get_goals(self) -> List[Goal]:
        """Get all goals"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM goals")
            return [
                Goal(
                    id=row['id'],
                    name=row['name'],
                    target=row['target'],
                    saved=row['saved'],
                    allocation=row['allocation'],
                    deadline=row['deadline'],
                    notified=bool(row['notified'])
                )
                for row in cursor.fetchall()
            ]
    
    def update_goal_saved(self, goal_id: int, amount: float) -> None:
        """Add amount to goal's saved value"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE goals SET saved = saved + ? WHERE id = ?",
                (amount, goal_id)
            )
    
    def mark_goal_notified(self, goal_id: int) -> None:
        """Mark goal as notified"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE goals SET notified = 1 WHERE id = ?", (goal_id,))
    
    def delete_goal(self, goal_id: int) -> None:
        """Delete a goal"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    
    # ========== Allowance Operations ==========
    
    def get_allowance(self) -> Tuple[Optional[float], Optional[str]]:
        """Get allowance amount and last applied date"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT amount, last_applied FROM allowance WHERE id = 1")
            row = cursor.fetchone()
            return (row[0], row[1]) if row else (None, None)
    
    def set_allowance(self, amount: float) -> None:
        """Set monthly allowance amount"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO allowance (id, amount, last_applied) VALUES (1, ?, ?)",
                (amount, None)
            )
    
    def update_allowance_applied(self, month_str: str) -> None:
        """Update last applied month"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE allowance SET last_applied = ? WHERE id = 1",
                (month_str,)
            )


# ==================== Custom Dialogs ====================

class AddGoalDialog(QDialog):
    """Dialog for adding a new goal"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add New Goal")
        self.setMinimumWidth(400)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QFormLayout(self)
        layout.setSpacing(12)
        
        # Name
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g., New Laptop")
        layout.addRow("Goal Name:", self.name_input)
        
        # Target amount
        self.target_input = QDoubleSpinBox()
        self.target_input.setRange(0.01, 1000000)
        self.target_input.setPrefix("€ ")
        self.target_input.setValue(100)
        layout.addRow("Target Amount:", self.target_input)
        
        # Allocation percentage
        self.allocation_input = QSpinBox()
        self.allocation_input.setRange(1, 100)
        self.allocation_input.setSuffix(" %")
        self.allocation_input.setValue(10)
        layout.addRow("Allocation:", self.allocation_input)
        
        # Deadline
        self.deadline_input = QDateEdit()
        self.deadline_input.setCalendarPopup(True)
        self.deadline_input.setDate(QDate.currentDate().addMonths(6))
        self.deadline_input.setDisplayFormat("yyyy-MM-dd")
        layout.addRow("Deadline:", self.deadline_input)

        # Initial funding options
        self.fund_now_chk = QCheckBox("Fund from current balance now")
        self.initial_amount = QDoubleSpinBox()
        self.initial_amount.setRange(0.00, 1000000)
        self.initial_amount.setPrefix("€ ")
        self.initial_amount.setValue(0.00)
        self.initial_amount.setEnabled(False)
        self.fund_now_chk.toggled.connect(self.initial_amount.setEnabled)
        layout.addRow(self.fund_now_chk)
        layout.addRow("Initial amount:", self.initial_amount)
        
        # Buttons
        button_layout = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        button_layout.addWidget(btn_cancel)
        button_layout.addWidget(btn_save)
        layout.addRow(button_layout)
    
    def get_goal(self) -> Optional[tuple]:
        """Get the goal data and initial funding choice from dialog"""
        if not self.name_input.text().strip():
            return None
        
        goal = Goal(
            id=None,
            name=self.name_input.text().strip(),
            target=self.target_input.value(),
            saved=0,
            allocation=self.allocation_input.value(),
            deadline=self.deadline_input.date().toString("yyyy-MM-dd"),
            notified=False
        )
        return (goal, self.fund_now_chk.isChecked(), self.initial_amount.value())


class TransactionDialog(QDialog):
    """Dialog for adding transactions"""
    
    def __init__(self, transaction_type: str, parent=None):
        super().__init__(parent)
        self.transaction_type = transaction_type
        self.setWindowTitle(f"{transaction_type} Money")
        self.setMinimumWidth(350)
        self.setup_ui()
    
    def setup_ui(self):
        layout = QFormLayout(self)
        layout.setSpacing(12)
        
        # Amount
        self.amount_input = QDoubleSpinBox()
        self.amount_input.setRange(0.01, 1000000)
        self.amount_input.setPrefix("€ ")
        self.amount_input.setValue(10)
        layout.addRow("Amount:", self.amount_input)
        
        # Note
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Optional note")
        layout.addRow("Note:", self.note_input)
        
        # Buttons
        button_layout = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_cancel = QPushButton("Cancel")
        
        btn_save.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        
        button_layout.addWidget(btn_cancel)
        button_layout.addWidget(btn_save)
        layout.addRow(button_layout)
    
    def get_transaction(self) -> Tuple[float, str]:
        """Get transaction data"""
        amount = self.amount_input.value()
        note = self.note_input.text().strip() or self.transaction_type
        return (amount, note)


class ReportsSettingsDialog(QDialog):
    """Dialog for configuring email and auto-report settings"""

    def __init__(self, db: DatabaseManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Report Settings")
        self.setMinimumWidth(420)
        self.setup_ui()

    def setup_ui(self):
        form_layout = QFormLayout(self)
        form_layout.setSpacing(10)

        settings = self.db.get_settings()

        self.smtp_server = QLineEdit(settings.get('smtp_server', 'smtp.gmail.com'))
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(settings.get('smtp_port', 465)))
        self.use_ssl = QCheckBox("Use SSL")
        self.use_ssl.setChecked(bool(settings.get('use_ssl', 1)))
        self.sender_email = QLineEdit(settings.get('sender_email', ''))
        self.sender_password = QLineEdit(settings.get('sender_password', ''))
        self.sender_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.recipient_email = QLineEdit(settings.get('recipient_email', ''))
        self.auto_email = QCheckBox("Auto-send monthly report on startup")
        self.auto_email.setChecked(bool(settings.get('auto_email', 0)))

        form_layout.addRow("SMTP Server:", self.smtp_server)
        form_layout.addRow("SMTP Port:", self.smtp_port)
        form_layout.addRow(self.use_ssl)
        form_layout.addRow("Sender Email:", self.sender_email)
        form_layout.addRow("Sender App Password:", self.sender_password)
        form_layout.addRow("Recipient Email:", self.recipient_email)
        form_layout.addRow(self.auto_email)

        # Buttons
        buttons = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_save = QPushButton("Save")
        btn_cancel.clicked.connect(self.reject)
        btn_save.clicked.connect(self.on_save)
        buttons.addWidget(btn_cancel)
        buttons.addWidget(btn_save)
        form_layout.addRow(buttons)

    def on_save(self):
        s = {
            'smtp_server': self.smtp_server.text().strip() or 'smtp.gmail.com',
            'smtp_port': int(self.smtp_port.value()),
            'use_ssl': 1 if self.use_ssl.isChecked() else 0,
            'sender_email': self.sender_email.text().strip(),
            'sender_password': self.sender_password.text(),
            'recipient_email': self.recipient_email.text().strip(),
            'auto_email': 1 if self.auto_email.isChecked() else 0,
        }
        self.db.save_settings(s)
        self.accept()


# ==================== Custom Widgets ====================

class GoalCard(QFrame):
    """Widget displaying a single goal"""
    
    delete_requested = Signal(int)
    
    def __init__(self, goal: Goal, parent=None):
        super().__init__(parent)
        self.goal = goal
        self.setObjectName("goalCard")
        self.setup_ui()
    
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 16, 16, 16)
        
        # Header
        header_layout = QHBoxLayout()
        name_label = QLabel(self.goal.name)
        name_label.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        header_layout.addWidget(name_label)
        
        allocation_label = QLabel(f"{self.goal.allocation}%")
        allocation_label.setStyleSheet("color: #3fa9f5; font-weight: bold;")
        header_layout.addWidget(allocation_label)
        
        header_layout.addStretch()
        
        # Delete button
        delete_btn = QPushButton("×")
        delete_btn.setFixedSize(24, 24)
        delete_btn.setStyleSheet("""
            QPushButton {
                background-color: #ff4444;
                color: white;
                border-radius: 12px;
                font-weight: bold;
                font-size: 16px;
            }
            QPushButton:hover { background-color: #ff6666; }
        """)
        delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.goal.id))
        header_layout.addWidget(delete_btn)
        
        layout.addLayout(header_layout)
        
        # Progress bar
        progress_bar = QProgressBar()
        progress_bar.setValue(self.goal.progress_percentage)
        progress_bar.setTextVisible(True)
        progress_bar.setFormat(f"{self.goal.progress_percentage}%")
        layout.addWidget(progress_bar)
        
        # Amount info
        amount_label = QLabel(f"€ {self.goal.saved:.2f} / € {self.goal.target:.2f}")
        amount_label.setStyleSheet("color: #cccccc;")
        layout.addWidget(amount_label)
        
        # Deadline info
        days = self.goal.days_remaining
        deadline_color = "#ff4444" if days < 7 else "#3fa9f5"
        deadline_label = QLabel(f"⏳ {days} days remaining")
        deadline_label.setStyleSheet(f"color: {deadline_color};")
        layout.addWidget(deadline_label)


# ==================== Main Window ====================

class MoneyTrackerApp(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.db = DatabaseManager()
        self.setup_window()
        self.setup_ui()
        self.apply_styles()
        
        # Apply monthly allowance on startup
        self.apply_monthly_allowance()
        
        # Load initial data
        self.refresh_balance()
        self.refresh_goals()
        self.check_goal_notifications()

        # Auto-send monthly report if enabled in settings
        settings = self.db.get_settings()
        if settings.get('auto_email', 0):
            self.auto_send_monthly_report()
    
    def setup_window(self):
        """Configure main window"""
        self.setWindowTitle("Money Tracker Pro")
        self.resize(620, 820)
        self.setMinimumSize(500, 820)
    
    def setup_ui(self):
        """Create UI elements"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(20)
        main_layout.setContentsMargins(24, 24, 24, 24)
        
        # ========== Header ==========
        header = QLabel("Money Tracker Pro")
        header.setFont(QFont("Segoe UI", 24, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(header)
        
        # ========== Balance Card ==========
        balance_card = QFrame()
        balance_card.setObjectName("balanceCard")
        balance_layout = QVBoxLayout(balance_card)
        balance_layout.setSpacing(8)
        
        balance_title = QLabel("Current Balance")
        balance_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        balance_title.setStyleSheet("color: #aaaaaa; font-size: 14px;")
        
        self.balance_label = QLabel("€ 0.00")
        self.balance_label.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        self.balance_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        balance_layout.addWidget(balance_title)
        balance_layout.addWidget(self.balance_label)
        main_layout.addWidget(balance_card)
        
        # ========== Transaction Buttons ==========
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)
        
        btn_add = QPushButton("+ Add Money")
        btn_add.setMinimumHeight(50)
        btn_add.clicked.connect(self.handle_add_money)
        
        btn_spend = QPushButton("− Spend Money")
        btn_spend.setMinimumHeight(50)
        btn_spend.clicked.connect(self.handle_spend_money)
        
        button_layout.addWidget(btn_add)
        button_layout.addWidget(btn_spend)
        main_layout.addLayout(button_layout)
        
        # ========== Goals Section ==========
        goals_group = QGroupBox("Savings Goals")
        goals_group.setObjectName("goalsGroup")
        goals_layout = QVBoxLayout(goals_group)
        
        # Goals header
        goals_header = QHBoxLayout()
        add_goal_btn = QPushButton("+ Add Goal")
        add_goal_btn.clicked.connect(self.handle_add_goal)
        goals_header.addStretch()
        goals_header.addWidget(add_goal_btn)
        goals_layout.addLayout(goals_header)
        
        # Scrollable goals area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        scroll_content = QWidget()
        self.goals_layout = QVBoxLayout(scroll_content)
        self.goals_layout.setSpacing(12)
        self.goals_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        goals_layout.addWidget(scroll)
        
        main_layout.addWidget(goals_group)
        
        # ========== Reports Section ==========
        reports_group = QGroupBox("Reports")
        reports_layout = QHBoxLayout(reports_group)
        reports_layout.setSpacing(12)

        btn_export = QPushButton("📤 Export Month")
        btn_export.setMinimumHeight(45)
        btn_export.clicked.connect(self.export_monthly_csv)

        btn_preview = QPushButton("📈 Preview Chart")
        btn_preview.setMinimumHeight(45)
        btn_preview.clicked.connect(self.preview_monthly_chart)

        btn_email = QPushButton("📧 Send Report")
        btn_email.setMinimumHeight(45)
        btn_email.clicked.connect(self.manual_send_report)

        btn_settings = QPushButton("⚙️ Settings")
        btn_settings.setMinimumHeight(45)
        btn_settings.clicked.connect(self.open_report_settings)

        reports_layout.addWidget(btn_export)
        reports_layout.addWidget(btn_preview)
        reports_layout.addWidget(btn_email)
        reports_layout.addWidget(btn_settings)

        main_layout.addWidget(reports_group)
    
    def apply_styles(self):
        """Apply stylesheet"""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f0f0f;
            }
            
            QLabel {
                color: #ffffff;
            }
            
            QPushButton {
                background-color: #1e1e1e;
                color: #ffffff;
                border: none;
                border-radius: 12px;
                padding: 12px 20px;
                font-size: 14px;
                font-weight: 500;
            }
            
            QPushButton:hover {
                background-color: #2a2a2a;
            }
            
            QPushButton:pressed {
                background-color: #1a1a1a;
            }
            
            QFrame#balanceCard {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1a1a2e,
                    stop:1 #16213e
                );
                border-radius: 20px;
                padding: 30px;
            }
            
            QGroupBox {
                color: #ffffff;
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #2a2a2a;
                border-radius: 16px;
                margin-top: 12px;
                padding-top: 20px;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 16px;
                padding: 0 8px;
            }
            
            QFrame#goalCard {
                background-color: #1a1a1a;
                border: 1px solid #2a2a2a;
                border-radius: 12px;
            }
            
            QProgressBar {
                height: 12px;
                border-radius: 6px;
                background-color: #2a2a2a;
                text-align: center;
            }
            
            QProgressBar::chunk {
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3fa9f5,
                    stop:1 #6bc5ff
                );
                border-radius: 6px;
            }
            
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            
            QDialog {
                background-color: #1a1a1a;
            }
            
            QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit {
                background-color: #2a2a2a;
                color: #ffffff;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
                padding: 8px;
                font-size: 13px;
            }
            
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus {
                border: 1px solid #3fa9f5;
            }
        """)
    
    # ========== Balance Operations ==========
    
    def refresh_balance(self):
        """Update balance display"""
        balance = self.db.get_balance()
        self.balance_label.setText(f"€ {balance:,.2f}")
    
    # ========== Transaction Handlers ==========
    
    def handle_add_money(self):
        """Handle adding money"""
        dialog = TransactionDialog("Income", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            amount, note = dialog.get_transaction()
            self.db.add_transaction(amount, note)
            self.allocate_to_goals(amount)
            self.refresh_balance()
            self.refresh_goals()
            self.check_goal_notifications()
    
    def handle_spend_money(self):
        """Handle spending money"""
        dialog = TransactionDialog("Expense", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            amount, note = dialog.get_transaction()
            self.db.add_transaction(-amount, note)
            self.refresh_balance()
    
    # ========== Goal Operations ==========
    
    def handle_add_goal(self):
        """Handle adding a new goal, with optional initial funding from balance"""
        dialog = AddGoalDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            result = dialog.get_goal()
            if result:
                goal, fund_now, initial_amount = result
                # Validate total allocation
                current_goals = self.db.get_goals()
                total_allocation = sum(g.allocation for g in current_goals) + goal.allocation
                
                if total_allocation > 100:
                    QMessageBox.warning(
                        self,
                        "Invalid Allocation",
                        f"Total allocation would be {total_allocation}%. Maximum is 100%."
                    )
                    return
                
                new_goal_id = self.db.add_goal(goal)

                # Optional initial funding (earmark from current balance)
                if fund_now and initial_amount and initial_amount > 0:
                    balance = self.db.get_balance()
                    if initial_amount > balance:
                        QMessageBox.warning(
                            self,
                            "Insufficient Balance",
                            f"Initial funding (€ {initial_amount:.2f}) exceeds current balance (€ {balance:.2f})."
                        )
                    else:
                        self.db.update_goal_saved(new_goal_id, initial_amount)
                
                self.refresh_goals()
    
    def refresh_goals(self):
        """Refresh goals display"""
        # Clear existing goals
        while self.goals_layout.count() > 1:  # Keep stretch
            item = self.goals_layout.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        
        # Add goal cards
        goals = self.db.get_goals()
        for goal in goals:
            card = GoalCard(goal)
            card.delete_requested.connect(self.handle_delete_goal)
            self.goals_layout.insertWidget(self.goals_layout.count() - 1, card)
    
    def handle_delete_goal(self, goal_id: int):
        """Handle goal deletion"""
        reply = QMessageBox.question(
            self,
            "Delete Goal",
            "Are you sure you want to delete this goal?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_goal(goal_id)
            self.refresh_goals()
    
    def allocate_to_goals(self, income: float):
        """Distribute income to goals based on allocation"""
        goals = self.db.get_goals()
        for goal in goals:
            if goal.id is not None:
                allocation_amount = income * (goal.allocation / 100)
                self.db.update_goal_saved(goal.id, allocation_amount)
    
    def check_goal_notifications(self):
        """Check and notify for completed goals"""
        goals = self.db.get_goals()
        for goal in goals:
            if goal.saved >= goal.target and not goal.notified:
                QMessageBox.information(
                    self,
                    "Goal Achieved! 🎉",
                    f"Congratulations! You've reached your goal: {goal.name}\n\n"
                    f"Target: € {goal.target:.2f}\n"
                    f"Saved: € {goal.saved:.2f}"
                )
                if goal.id is not None:
                    self.db.mark_goal_notified(goal.id)
    
    # ========== Monthly Allowance ==========
    
    def apply_monthly_allowance(self):
        """Apply monthly allowance if due"""
        current_month = datetime.now().strftime("%Y-%m")
        amount, last_applied = self.db.get_allowance()
        
        if amount and amount > 0 and last_applied != current_month:
            self.db.add_transaction(amount, "Monthly Allowance")
            self.db.update_allowance_applied(current_month)
            self.allocate_to_goals(amount)
    
    # ========== Export and Reports ==========
    
    def export_monthly_csv(self):
        """Export current month's transactions to CSV"""
        now = datetime.now()
        filename = f"finance_{now.year}_{now.month:02d}.csv"
        
        try:
            transactions = self.db.get_monthly_transactions(now.year, now.month)
            
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Amount", "Date", "Balance"])
                
                balance = 0
                for amount, date_str in transactions:
                    balance += amount
                    writer.writerow([f"{amount:.2f}", date_str, f"{balance:.2f}"])
            
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported to {filename}"
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Failed to export data: {str(e)}"
            )

    def create_monthly_chart(self, year: int, month: int) -> str:
        """Create balance chart for the month and save as PNG. Returns filename."""
        transactions = self.db.get_monthly_transactions(year, month)
        
        dates = []
        balances = []
        balance = 0
        
        for amount, date_str in transactions:
            balance += amount
            dates.append(date_str[:10])  # Just the date part
            balances.append(balance)
        
        # Create chart
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(10, 6))
        
        if dates and balances:
            ax.plot(dates, balances, marker='o', linewidth=2, markersize=6, color='#3fa9f5')
            ax.fill_between(range(len(balances)), balances, alpha=0.3, color='#3fa9f5')
        
        ax.set_title(f"Balance Trend - {year}-{month:02d}", fontsize=16, pad=20)
        ax.set_xlabel("Date", fontsize=12)
        ax.set_ylabel("Balance (€)", fontsize=12)
        ax.grid(True, alpha=0.2)
        
        # Rotate date labels
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        # Save chart
        filename = f"balance_{year}_{month:02d}.png"
        plt.savefig(filename, dpi=150, facecolor='#1a1a1a')
        plt.close()
        
        return filename

    def preview_monthly_chart(self):
        now = datetime.now()
        filename = self.create_monthly_chart(now.year, now.month)
        QMessageBox.information(
            self,
            "Chart Generated",
            f"Saved chart to {filename}."
        )

    def send_email_report(self, update_last_sent: bool = False) -> bool:
        """Send monthly financial report via email using configured settings"""
        settings = self.db.get_settings()
        required = ['sender_email', 'sender_password', 'recipient_email']
        if any(not settings.get(k) for k in required):
            reply = QMessageBox.question(
                self,
                "Email Not Configured",
                "Email settings are incomplete. Do you want to open Report Settings now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.open_report_settings()
            return False

        try:
            now = datetime.now()
            year, month = now.year, now.month
            
            # Create chart
            chart_file = self.create_monthly_chart(year, month)
            
            # Get summary
            income, expense = self.db.get_monthly_summary(year, month)
            net = income + expense  # expense is negative
            
            # Create email
            msg = EmailMessage()
            msg["Subject"] = f"Financial Report — {now.strftime('%B %Y')}"
            msg["From"] = settings['sender_email']
            msg["To"] = settings['recipient_email']
            
            # Email body
            msg.set_content(f"""
Monthly Financial Summary - {now.strftime('%B %Y')}
{'='*50}

Income:   € {income:,.2f}
Expenses: € {abs(expense):,.2f}
Net:      € {net:,.2f}

Current Balance: € {self.db.get_balance():,.2f}

{'='*50}
This is an automated report from Money Tracker Pro.
            """)
            
            # Attach chart
            with open(chart_file, "rb") as f:
                msg.add_attachment(
                    f.read(),
                    maintype="image",
                    subtype="png",
                    filename=chart_file
                )
            
            # Send email
            if settings.get('use_ssl', 1):
                with smtplib.SMTP_SSL(settings.get('smtp_server', 'smtp.gmail.com'), int(settings.get('smtp_port', 465))) as server:
                    server.login(settings['sender_email'], settings['sender_password'])
                    server.send_message(msg)
            else:
                with smtplib.SMTP(settings.get('smtp_server', 'smtp.gmail.com'), int(settings.get('smtp_port', 465))) as server:
                    server.starttls()
                    server.login(settings['sender_email'], settings['sender_password'])
                    server.send_message(msg)
            
            # Update last sent
            if update_last_sent:
                self.db.update_email_sent(f"{year}-{month:02d}")
            
            return True
            
        except Exception as e:
            QMessageBox.critical(
                self,
                "Email Failed",
                f"Failed to send email report: {str(e)}"
            )
            return False

    def auto_send_monthly_report(self):
        """Automatically send monthly report if not sent this month and enabled"""
        now = datetime.now()
        current_month = f"{now.year}-{now.month:02d}"
        last_sent = self.db.get_last_email_sent()
        
        if last_sent != current_month:
            sent = self.send_email_report(update_last_sent=True)
            if sent:
                QMessageBox.information(self, "Report Sent", "Monthly report sent automatically.")

    def manual_send_report(self):
        """Manually send monthly report"""
        if self.send_email_report(update_last_sent=False):
            QMessageBox.information(
                self,
                "Report Sent",
                "Monthly financial report has been sent successfully."
            )

    def open_report_settings(self):
        dlg = ReportsSettingsDialog(self.db, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            QMessageBox.information(self, "Settings Saved", "Report settings have been updated.")


# ==================== Application Entry Point ====================

def main():
    """Application entry point"""
    app = QApplication(sys.argv)
    
    # Set application info
    app.setApplicationName("Money Tracker Pro")
    app.setOrganizationName("Personal Finance")
    
    # Create and show main window
    window = MoneyTrackerApp()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()