@bot.tree.command(name="atualizar_produto")
async def atualizar_produto(interaction: discord.Interaction, id: str, novo_nome: str = None, novo_preco: float = None, nova_descricao: str = None):
    """
    Atualiza as informações de um produto existente mantendo o estoque
    """
    if interaction.user.id != MEU_ID:
        await interaction.response.send_message("❌ Apenas o dono pode usar este comando!", ephemeral=True)
        return
    
    if id not in produtos_disponiveis:
        await interaction.response.send_message(f"❌ Produto com ID `{id}` não encontrado!", ephemeral=True)
        return
    
    # Verifica se pelo menos um campo foi fornecido para atualizar
    if not any([novo_nome, novo_preco is not None, nova_descricao]):
        await interaction.response.send_message(
            "❌ Você precisa fornecer pelo menos um campo para atualizar!\n"
            "Exemplo: `/atualizar_produto id:produto1 novo_nome:'Novo Nome'`",
            ephemeral=True
        )
        return
    
    produto = produtos_disponiveis[id]
    atualizacoes = []
    
    # Atualiza o nome se fornecido
    if novo_nome:
        produto["nome"] = novo_nome
        atualizacoes.append(f"📝 Nome: `{novo_nome}`")
    
    # Atualiza o preço se fornecido
    if novo_preco is not None:
        produto["preco"] = novo_preco
        atualizacoes.append(f"💰 Preço: `R$ {novo_preco:.2f}`")
    
    # Atualiza a descrição se fornecida
    if nova_descricao:
        desc_formatada = nova_descricao.replace("|", "\n")
        produto["descricao"] = desc_formatada
        # Mostra apenas as primeiras 50 caracteres da descrição
        preview = nova_descricao[:50] + "..." if len(nova_descricao) > 50 else nova_descricao
        atualizacoes.append(f"📄 Descrição: `{preview}`")
    
    # Salva as alterações
    salvar_json("produtos.json", produtos_disponiveis)
    
    # Informações do estoque atual
    qtd_estoque = verificar_estoque(id)
    qtd_variacoes = len(produto.get("variacoes", []))
    
    # Cria embed de confirmação
    emb = discord.Embed(
        title="✅ PRODUTO ATUALIZADO COM SUCESSO!",
        description=f"Produto `{id}` atualizado com sucesso!",
        color=0x00ff88
    )
    
    # Adiciona as atualizações feitas
    if atualizacoes:
        emb.add_field(
            name="📋 Alterações realizadas:",
            value="\n".join(atualizacoes),
            inline=False
        )
    
    # Adiciona informações do estoque
    emb.add_field(
        name="📦 Status do Estoque:",
        value=f"• Itens disponíveis: `{qtd_estoque}`\n"
              f"• Variações: `{qtd_variacoes}`\n"
              f"• ID do produto: `{id}`",
        inline=False
    )
    
    # Adiciona informações completas do produto atualizado
    emb.add_field(
        name="📦 Dados completos do produto:",
        value=f"**Nome:** {produto['nome']}\n"
              f"**Preço:** R$ {produto['preco']:.2f}\n"
              f"**Descrição:** {produto['descricao'][:100]}{'...' if len(produto['descricao']) > 100 else ''}",
        inline=False
    )
    
    # Adiciona as variações se existirem
    if produto.get("variacoes"):
        vars_text = ""
        for v in produto["variacoes"]:
            vars_text += f"• {v['nome']} - R$ {v['preco']:.2f}\n"
        emb.add_field(
            name="🎨 Variações disponíveis:",
            value=vars_text or "Nenhuma variação cadastrada",
            inline=False
        )
    
    emb.set_footer(text=f"Atualizado em {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    await interaction.response.send_message(embed=emb, ephemeral=True)
