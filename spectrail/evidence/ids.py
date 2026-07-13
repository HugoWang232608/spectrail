def page_id(page: int) -> str:
    _require_positive(page, "page")
    return f"page_{page:04d}"


def table_id(index: int) -> str:
    _require_positive(index, "table index")
    return f"tbl_{index:08d}"


def cell_id(table_index: int, row_index: int, column_index: int) -> str:
    _require_positive(table_index, "table index")
    _require_positive(row_index, "row index")
    _require_positive(column_index, "column index")
    return f"cell_{table_index:08d}_r{row_index:04d}_c{column_index:04d}"


def fragment_id(block_identifier: str, index: int) -> str:
    if not block_identifier:
        raise ValueError("block identifier must not be empty")
    _require_positive(index, "fragment index")
    return f"frag_{block_identifier}_{index:04d}"


def occurrence_id(index: int) -> str:
    _require_positive(index, "occurrence index")
    return f"occ_{index:08d}"


def _require_positive(value: int, label: str) -> None:
    if value < 1:
        raise ValueError(f"{label} must be positive")
