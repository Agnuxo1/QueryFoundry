# Experimentos conservados

`rejected_sparse_positions.py` es un prototipo descartado. Su gate sintético
detectó cambios en los errores SQL: con declaración vacía/incorrecta y peso
malformado, la receta original rechaza y el prototipo acepta. No es un modo
de producción ni se promociona con resultados de velocidad.

Reproducir con `python scripts/test_sparse_semantics.py` sobre el laboratorio
100k activo. El comando devuelve error de gate esperado; detalles en
`reports/sparse-semantics.json`. No modifica las entradas oficiales: usa CTEs.
