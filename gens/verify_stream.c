/* verify_stream.c: check a word read from standard input, without storing it, for every permutation of n symbols.

   usage: verify_stream N ALPHABET [THREADS] [--shard I/K]   (N <= 16; ALPHABET lists the N symbols in the order 0..N-1)

   --shard I/K: only the permutations whose rank r lies in [floor(I*N!/K), floor((I+1)*N!/K)) are tracked, in a table of
   that range only (memory about N!/(8K)); "missing" and "distinct_seen" then refer to the range, the output line gets
   the fields "shard":"I/K","lo":..,"hi":.., and the exit code is 0 iff every permutation of the range was seen (and no
   foreign byte, windows as expected).  Without --shard the output and behaviour are as before.

   A reader thread fills blocks of 64 MiB; each block starts with the last N - 1 symbols of the block before, so a
   window is never lost at a block edge.  Worker threads compute, for every window of N symbols that holds N distinct
   symbols, the rank of the permutation (Lehmer code) and set its bit in a table of N! bits with an atomic OR.
   At the end it prints the length, the number of windows, of windows that are permutations, of first occurrences,
   of repeats, of permutations never seen, and of bytes outside the alphabet (newlines at the very end are allowed).
   Exit code 0 only if every permutation occurs and no byte is foreign.  Memory: N!/8 bytes (10.9 GB for N = 14). */
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#define BLOCK (64u << 20)
#define NBUF 12

static int N, NT;
static unsigned char map[256];
static uint64_t fact[17];
static _Atomic uint64_t *bits;
static uint64_t nbits;           /* N! */
static uint64_t lo, hi, span;    /* tracked ranks [lo, hi), span = hi - lo (everything without --shard) */

typedef struct { unsigned char *data; size_t len; int last; } Buf;
static Buf bufs[NBUF];
static int qhead, qtail, qcount, done_reading;
static int freelist[NBUF], nfree;
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t cv_full = PTHREAD_COND_INITIALIZER, cv_free = PTHREAD_COND_INITIALIZER;
static int queue[NBUF];

static _Atomic uint64_t windows, perms, firsts, foreign_bytes, range_windows;

static void *worker(void *arg) {
  (void)arg;
  for (;;) {
    pthread_mutex_lock(&mu);
    while (qcount == 0 && !done_reading) pthread_cond_wait(&cv_full, &mu);
    if (qcount == 0 && done_reading) { pthread_mutex_unlock(&mu); return NULL; }
    int b = queue[qhead]; qhead = (qhead + 1) % NBUF; qcount--;
    pthread_mutex_unlock(&mu);
    unsigned char *d = bufs[b].data; size_t len = bufs[b].len;
    uint64_t w = 0, p = 0, f = 0, rw = 0;
    /* counts of each symbol in the current window and the number of symbols that occur at least twice */
    int cnt[16] = {0}, dup = 0;
    for (size_t i = 0; i < len; i++) {
      int s = d[i];
      if (cnt[s]++ == 1) dup++;
      if (i >= (size_t)N) { int t = d[i - N]; if (--cnt[t] == 1) dup--; }
      if (i + 1 < (size_t)N) continue;
      w++;
      if (dup) continue;
      p++;
      { uint64_t blo = (uint64_t)d[i + 1 - N] * fact[N - 1];    /* the first letter fixes r / (N-1)! */
        if (blo + fact[N - 1] <= lo || blo >= hi) continue; }
      uint64_t r = 0; unsigned left = (1u << N) - 1;
      const unsigned char *x = d + i + 1 - N;
      for (int j = 0; j < N - 1; j++) {
        unsigned below = left & ((1u << x[j]) - 1);
        r += (uint64_t)__builtin_popcount(below) * fact[N - 1 - j];
        left &= ~(1u << x[j]);
      }
      if (r < lo || r >= hi) continue;
      r -= lo; rw++;
      uint64_t m = 1ull << (r & 63);
      uint64_t old = atomic_fetch_or_explicit(&bits[r >> 6], m, memory_order_relaxed);
      if (!(old & m)) f++;
    }
    atomic_fetch_add(&windows, w); atomic_fetch_add(&perms, p); atomic_fetch_add(&firsts, f); atomic_fetch_add(&range_windows, rw);
    pthread_mutex_lock(&mu);
    freelist[nfree++] = b;
    pthread_cond_signal(&cv_free);
    pthread_mutex_unlock(&mu);
  }
}

int main(int argc, char **argv) {
  int sharded = 0; unsigned long shard_i = 0, shard_k = 1;
  for (int a = 1; a < argc; a++) if (!strcmp(argv[a], "--shard")) {
    char *e1, *e2;
    if (a + 1 >= argc) { fprintf(stderr, "--shard needs I/K\n"); return 2; }
    shard_i = strtoul(argv[a + 1], &e1, 10);
    if (*e1 != '/') { fprintf(stderr, "--shard needs I/K\n"); return 2; }
    shard_k = strtoul(e1 + 1, &e2, 10);
    if (*e2 || shard_k < 1 || shard_k > 100000 || shard_i >= shard_k) { fprintf(stderr, "bad --shard\n"); return 2; }
    sharded = 1;
    for (int b = a; b + 2 < argc; b++) argv[b] = argv[b + 2];
    argc -= 2; break;
  }
  if (argc < 3) { fprintf(stderr, "usage: verify_stream N ALPHABET [THREADS] [--shard I/K]\n"); return 2; }
  N = atoi(argv[1]); NT = argc > 3 ? atoi(argv[3]) : 4;
  if (N < 2 || N > 16 || (int)strlen(argv[2]) != N || NT < 1 || NT > 64) { fprintf(stderr, "bad arguments\n"); return 2; }
  memset(map, 0xFF, sizeof map);
  for (int i = 0; i < N; i++) {
    if (map[(unsigned char)argv[2][i]] != 0xFF) { fprintf(stderr, "repeated symbol in alphabet\n"); return 2; }
    map[(unsigned char)argv[2][i]] = (unsigned char)i;
  }
  fact[0] = 1; for (int i = 1; i <= 16; i++) fact[i] = fact[i - 1] * i;
  nbits = fact[N];
  if (shard_k > nbits) { fprintf(stderr, "bad --shard\n"); return 2; }
  lo = shard_i * nbits / shard_k; hi = (shard_i + 1) * nbits / shard_k; span = hi - lo;
  bits = calloc(span / 64 + 1, sizeof(uint64_t));
  if (!bits) { fprintf(stderr, "cannot allocate %llu bytes\n", (unsigned long long)(span / 8)); return 2; }
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
#endif
  for (int i = 0; i < NBUF; i++) { bufs[i].data = malloc(BLOCK + 16); if (!bufs[i].data) return 2; freelist[nfree++] = i; }
  pthread_t th[64];
  for (int i = 0; i < NT; i++) pthread_create(&th[i], NULL, worker, NULL);
  unsigned char carry[16]; size_t ncarry = 0, total = 0, trailing_nl = 0;
  unsigned char *raw = malloc(BLOCK);
  for (;;) {
    size_t got = fread(raw, 1, BLOCK - 16, stdin);
    if (got == 0) break;
    pthread_mutex_lock(&mu);
    while (nfree == 0) pthread_cond_wait(&cv_free, &mu);
    int b = freelist[--nfree];
    pthread_mutex_unlock(&mu);
    unsigned char *d = bufs[b].data;
    memcpy(d, carry, ncarry);
    size_t len = ncarry;
    for (size_t i = 0; i < got; i++) {
      unsigned char c = raw[i];
      if (c == '\n' || c == '\r') { trailing_nl++; continue; }
      if (trailing_nl) { atomic_fetch_add(&foreign_bytes, trailing_nl); trailing_nl = 0; }
      unsigned char s = map[c];
      if (s == 0xFF) { atomic_fetch_add(&foreign_bytes, 1); continue; }
      d[len++] = s; total++;
    }
    bufs[b].len = len;
    ncarry = len < (size_t)(N - 1) ? len : (size_t)(N - 1);
    memcpy(carry, d + len - ncarry, ncarry);
    pthread_mutex_lock(&mu);
    queue[qtail] = b; qtail = (qtail + 1) % NBUF; qcount++;
    pthread_cond_signal(&cv_full);
    pthread_mutex_unlock(&mu);
  }
  pthread_mutex_lock(&mu); done_reading = 1; pthread_cond_broadcast(&cv_full); pthread_mutex_unlock(&mu);
  for (int i = 0; i < NT; i++) pthread_join(th[i], NULL);
  uint64_t seen = 0;
  for (uint64_t i = 0; i < span / 64 + 1; i++) seen += (uint64_t)__builtin_popcountll(bits[i]);
  uint64_t expect_windows = total >= (size_t)N ? total - N + 1 : 0;
  printf("{\"n\":%d,\"length\":%llu,\"windows\":%llu,\"expected_windows\":%llu,\"permutation_windows\":%llu,"
         "\"first_occurrences\":%llu,\"repeats\":%llu,\"distinct_seen\":%llu,\"missing\":%llu,\"foreign_bytes\":%llu,"
         "\"trailing_newlines\":%llu",
         N, (unsigned long long)total, (unsigned long long)windows, (unsigned long long)expect_windows,
         (unsigned long long)perms, (unsigned long long)firsts, (unsigned long long)(range_windows - firsts),
         (unsigned long long)seen, (unsigned long long)(span - seen), (unsigned long long)foreign_bytes,
         (unsigned long long)trailing_nl);
  if (sharded) printf(",\"shard\":\"%lu/%lu\",\"lo\":%llu,\"hi\":%llu", shard_i, shard_k,
                      (unsigned long long)lo, (unsigned long long)hi);
  printf("}\n");
  return (seen == span && foreign_bytes == 0 && windows == expect_windows) ? 0 : 1;
}
