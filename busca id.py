import requests

API_KEY = "SUA_API_KEY_AQUI"
BASE_URL = "https://api.bling.com.br/Api/v3/contatos"

headers = {
    'Authorization': f'Bearer {API_KEY}',
    'Accept': 'application/json'
}
params = {'tipo': 'C'}
response = requests.get(BASE_URL, headers=headers, params=params)

print(response.json())  