"""analysis 層 — 蓄積データの集計と可視化用の素材づくり(Phase 1)。

DB(observations)から pandas DataFrame を作り、波形・ヒートマップ・人圧を計算する。
表示(Streamlit)は viz 層に置き、この層は数字だけを扱う(テスト可能に保つ)。
"""
