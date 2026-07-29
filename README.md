# LangChain Learning

This repository contains my LangChain learning notes, code examples, and small projects.

## Windows Setup

### 1. Clone the Repository

```powershell
git clone git@github.com:charlie6713/langchain-learning.git
cd langchain-learning
```

### 2. Create a Virtual Environment

```powershell
python -m venv .venv
```

### 3. Activate the Virtual Environment

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate it again:

```powershell
.\.venv\Scripts\Activate.ps1
```

### 4. Install Dependencies

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 5. Update Dependencies

After installing a new package:

```powershell
pip freeze > requirements.txt
```

### 6. Deactivate the Virtual Environment

```powershell
deactivate
```

## Important

Do not upload `.env`, API keys, or the `.venv` folder to GitHub.
