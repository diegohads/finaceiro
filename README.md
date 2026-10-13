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

Em desenvolvimento, `DEBUG` fica ativo por padrão e o Django aceita qualquer host. Em produção, defina `DEBUG=False` e configure `ALLOWED_HOSTS` com os domínios válidos separados por vírgulas (por exemplo, `ALLOWED_HOSTS=financeiro.exemplo.com,www.financeiro.exemplo.com`).

## Perfis de acesso

| Perfil | Como é criado | Acesso |
| --- | --- | --- |
| Usuário comum | Cadastro em `/cadastro/` | Dashboard, criação, edição e exclusão das próprias transações |
| Subusuário | Usuário comum cadastra em `/subusuarios/` | Dashboard e criação de lançamentos na conta principal; não pode editar nem excluir. O responsável pode bloquear ou excluir o acesso |
| Administrador | `createsuperuser` | Tudo do usuário comum e painel `/admin/` com transações de todos os usuários |

O cadastro público nunca cria `is_staff` ou `is_superuser`. O Django Admin controla o acesso administrativo usando o usuário padrão e suas permissões.

## Categorias e contas financeiras

Em `/cadastros/`, cada usuário pode cadastrar tipos de receita e contas financeiras (por exemplo, banco, carteira ou dinheiro). Despesas não usam categorias. Ao confirmar uma receita, selecione a conta onde o dinheiro entrou; ao baixar uma despesa, selecione a conta de onde saiu. Também é possível transferir saldo entre contas, editar contas ou excluí-las somente quando o saldo estiver zerado. O saldo de cada conta é calculado pelas receitas, despesas e transferências confirmadas e aparece no dashboard e nos cadastros.

## Personalizar documentos

Administradores podem configurar logos distintas em `/configuracao/documentos/`: menu (PNG/JPG, até 2 MB e 500 x 500 pixels), relatório (até 120 x 100 pixels) e recibo (até 200 x 200 pixels). Nos PDFs, cada logo é ajustada proporcionalmente ao respectivo espaço.

Recibos podem ser emitidos para empresas com CNPJ ou pessoas físicas com CPF válido. O PDF apresenta um layout compacto, ajusta a logo do recibo a até 52 x 52 pontos e inclui status, valor, dados do pagador e detalhes do pagamento.
