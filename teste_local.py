"""
Teste rápido do conector - roda a busca direto, sem precisar de
conector configurado no Claude nem nada. É só pra confirmar que a
raspagem do Planalto está funcionando.

COMO USAR
---------
Deixe o outro terminal (planalto_mcp_server.py) aberto rodando, se
quiser -- mas nem precisa: este script aqui não depende do servidor
estar de pé, ele testa a lógica de busca diretamente.

Na pasta onde estão os arquivos, rode:

    python teste_local.py

Vai perguntar o que você quer buscar. Digite, por exemplo:
    referencia: CLT
    artigo: 7

ou:
    referencia: lei 8.078/1990
    artigo: 6
"""

from planalto_mcp_server import buscar_artigo

print("=" * 60)
print("TESTE DO CONECTOR - busca de artigo no Planalto")
print("=" * 60)
print()
print("Exemplos de referencia: CF, CLT, CC, CPC, CP, CPP, CDC, CTN")
print("ou formato livre: 'lei 8.078/1990', 'decreto-lei 5.452/1943'")
print()

referencia = input("Digite a referencia da norma: ").strip()
artigo = input("Digite o numero do artigo: ").strip()
item = input("Digite o inciso/alinea (ou deixe em branco p/ artigo inteiro): ").strip()

print()
print("Buscando... (pode demorar alguns segundos)")
print()

resultado = buscar_artigo(referencia, artigo, item)

print("-" * 60)
print(resultado)
print("-" * 60)
