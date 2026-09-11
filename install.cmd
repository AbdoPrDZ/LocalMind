python -m virtualenv .venv

python -m pip cache purge
python -m pip install --default-timeout=100 --retries 10 -r .\requirements.txt
