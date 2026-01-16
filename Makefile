.PHONY: run clean
run:
	python main.py --region us_conus --cells 3500 --radius_m 20000 --max_per_cell 100
clean:
	rm -rf output/*.csv logs/* checkpoints/* 2>/dev/null || true
