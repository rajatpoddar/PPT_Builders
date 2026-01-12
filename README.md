# 🏗️ PPT Builders Manager

**PPT Builders Manager** is a comprehensive construction site management web application designed to streamline the tracking of labour, material expenses, site progress, and financial reports. Built with **Flask**, it offers a mobile-friendly interface (PWA) for easy access on-site.

## 🚀 Key Features

### 📊 Dashboard & Analytics
- **Project Overview:** Real-time tracking of multiple construction sites.
- **Financial Stats:** Automatic calculation of **Work Value (Income)** and **Net Profit** based on site progress and set rates.
- **Worker Stats:** Live count of Mistris, Labours, and Idle workers.
- **Progress Tracking:** Visualize site completion with progress bars and estimated completion days.

### 👷 Worker Management
- **Directory:** Manage detailed profiles for workers (Mistri/Labour) including photos, contact info, and daily wages.
- **Attendance System:** Easy-to-use interface to mark **Present**, **Half Day**, or **Absent**.
- **Rating System:** Rate workers based on performance.

### 💰 Expense & Payment Tracking
- **Material Expenses:** Log daily expenses (e.g., Cement, Sand) for specific projects.
- **Labour Payments:** Record payments made to workers and track "Total Earned" vs "Total Paid" (Due Balance).
- **Reports:** Generate print-friendly reports for **Expenses** and **Attendance**.

### 📱 PWA (Progressive Web App)
- Mobile-optimized design.
- Can be installed on phones as a native-like app.
- Offline-ready UI elements.

---

## 🛠️ Tech Stack

- **Backend:** Python (Flask)
- **Database:** SQLite
- **Frontend:** HTML5, Bootstrap 5, Jinja2 Templates
- **Containerization:** Docker, Docker Compose
- **Server:** Gunicorn

---

## ⚙️ Installation & Setup

### Option 1: Run Locally (Python)

1.  **Clone the Repository**
    ```bash
    git clone [https://github.com/yourusername/ppt-builders.git](https://github.com/yourusername/ppt-builders.git)
    cd ppt-builders
    ```

2.  **Create a Virtual Environment**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows use: venv\Scripts\activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Run the Application**
    ```bash
    python app.py
    ```
    Access the app at `http://127.0.0.1:5000`

### Option 2: Run with Docker (Recommended)

If you have Docker installed, you can run the app with a single command.

1.  **Build and Run**
    ```bash
    docker-compose up -d --build
    ```

2.  **Access the App**
    The app will be running at `http://localhost:7782` (as per `docker-compose.yml`).

---

## 📂 Project Structure