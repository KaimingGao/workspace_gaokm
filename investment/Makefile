.PHONY: lint format typecheck test compile check

## 质量工具：pip install -r requirements-dev.txt

## 代码质量检查（lint + typecheck）
lint:
	ruff check core/ quant/ agent/ services/ skills/ web/ research/

## 自动格式化
format:
	ruff format core/ quant/ agent/ services/ skills/ web/ research/

## 类型检查（渐进式，非 strict）
typecheck:
	mypy core/signal/ core/backtest/ core/risk/ core/research/ core/position/ core/portfolio_optimize.py

## 运行测试
test:
	python -m pytest tests/ -x -q

## 语法编译校验
compile:
	python -m compileall -q -j 4 core agent quant web skills services research

## 一键全检（compile + lint + typecheck）
check: compile lint typecheck
	@echo "✓ All checks passed"
