#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static dev_t device;
static char **excluded;
static uint64_t records, errors;

/* ARM64 little endian: path length, mode, blocks512, size, device, inode, links,
 * followed by path bytes. A zero-length path is the checked completion footer.
 * Mode zero with a nonempty path records errno instead of file metadata. */
static void emit(const char *path, uint32_t mode, uint64_t blocks, uint64_t size,
                 uint64_t dev, uint64_t inode, uint64_t links) {
    uint32_t head[] = {(uint32_t)strlen(path), mode};
    uint64_t values[] = {blocks, size, dev, inode, links};
    if (fwrite(head, sizeof(head), 1, stdout) != 1 ||
        fwrite(values, sizeof(values), 1, stdout) != 1 ||
        fwrite(path, 1, head[0], stdout) != head[0]) exit(3);
}

static void failure(const char *path, int error) {
    errors++;
    emit(path, 0, (uint64_t)error, 0, 0, 0, 0);
}

static int excluded_path(const char *path) {
    for (char **p = excluded; *p; p++) if (!strcmp(path, *p)) return 1;
    return 0;
}

static void walk(const char *path, const struct stat *st, int parent_fd,
                 const char *entry, unsigned depth) {
    if (excluded_path(path) || st->st_dev != device) return;
    emit(path, st->st_mode, st->st_blocks, st->st_size, st->st_dev, st->st_ino, st->st_nlink);
    records++;
    if (!S_ISDIR(st->st_mode)) return;
    /* Raw traversal may be much deeper than the eventual aggregated display. */
    if (depth > 4096) { failure(path, EOVERFLOW); return; }
    int fd = openat(parent_fd, entry, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0) { failure(path, errno); return; }
    DIR *dir = fdopendir(fd);
    if (!dir) { int error = errno; close(fd); failure(path, error); return; }
    for (;;) {
        errno = 0;
        struct dirent *child = readdir(dir);
        if (!child) { if (errno) failure(path, errno); break; }
        if (!strcmp(child->d_name, ".") || !strcmp(child->d_name, "..")) continue;
        char *next;
        if (asprintf(&next, "%s/%s", path, child->d_name) < 0) exit(4);
        if (!excluded_path(next)) {
            struct stat info;
            if (fstatat(fd, child->d_name, &info, AT_SYMLINK_NOFOLLOW) == 0)
                walk(next, &info, fd, child->d_name, depth + 1);
            else failure(next, errno);
        }
        free(next);
    }
    closedir(dir);
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    struct stat root;
    if (lstat(argv[1], &root) || !S_ISDIR(root.st_mode)) return 2;
    device = root.st_dev;
    excluded = argv + 2;
    setvbuf(stdout, NULL, _IOFBF, 262144);
    if (fwrite("ASTREE1\n", 1, 8, stdout) != 8) return 3;
    walk(argv[1], &root, AT_FDCWD, argv[1], 0);
    emit("", 0, errors, records, 0, 0, 0);
    if (fflush(stdout)) return 3;
    return errors ? 1 : 0;
}
