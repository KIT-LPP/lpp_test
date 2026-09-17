/*
 * 標準出力と標準エラーのバッファリングを切る。
 *
 * lpprun は出力を捕まえて送るのでパイプ越しに実行する。C の stdio は
 * 相手がパイプだとブロックバッファになるので、入力を促す表示がプログラムの
 * 終了まで画面に出ない。
 *
 * stdbuf(1) は使えない。LD_PRELOAD で割り込むので、サニタイザを有効にすると
 * "ASan runtime does not come first in initial library list" で落ちる。
 * コンストラクタから setvbuf を呼べば、その衝突がない。
 *
 * gcc の -include で全翻訳単位の先頭に差し込む。static なので重複定義に
 * ならず、複数回呼ばれても setvbuf は同じ状態にするだけである。
 */
#include <stdio.h>

static void __attribute__((constructor)) lpp_unbuffer_init(void) {
  setvbuf(stdout, NULL, _IONBF, 0);
  setvbuf(stderr, NULL, _IONBF, 0);
}
