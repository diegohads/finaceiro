# Financeiro Pessoal

Sistema financeiro pessoal feito com Django e SQLite.

## Executar

```powershell
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Acesse `http://127.0.0.1:8000/`. O painel administrativo fica em `/admin/`.

## Perfis de acesso

| Perfil | Como é criado | Acesso |
| --- | --- | --- |
| Usuário comum | Cadastro em `/cadastro/` | Dashboard, criação, edição e exclusão das próprias transações |
| Administrador | `createsuperuser` | Tudo do usuário comum e painel `/admin/` com transações de todos os usuários |

O cadastro público nunca cria `is_staff` ou `is_superuser`. O Django Admin controla o acesso administrativo usando o usuário padrão e suas permissões.
