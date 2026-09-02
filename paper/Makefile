# make figures → make part1 part2 → make review (PNG renders of every page)
export PATH := /Library/TeX/texbin:$(PATH)
LATEX = TEXINPUTS=../common: sh ../common/build.sh
.PHONY: all figures part1 part2 review arxiv clean
all: figures part1 part2
figures:
	cd .. && uv run --with matplotlib --with pandas --with pyarrow --with scipy python paper/common/scripts/make_figures.py
part1:
	cd part1 && $(LATEX) main.tex && cp main.pdf ../brain-is-another-embodiment-part1.pdf
part2:
	cd part2 && $(LATEX) main.tex && cp main.pdf ../brain-is-another-embodiment-part2.pdf
review:
	mkdir -p review && for p in 1 2; do pdftoppm -r 50 -png brain-is-another-embodiment-part$$p.pdf review/part$$p; done; ls review | wc -l
arxiv: part1 part2
	sh common/make_arxiv.sh
clean:
	cd part1 && latexmk -C -quiet; cd ../part2 && latexmk -C -quiet; rm -rf ../review
