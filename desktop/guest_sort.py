"""Merge-sort driver yielding real guest-array addresses to an ARM comparator."""


def comparisons(base, count, size, original):
    order = list(range(count))
    width = 1
    while width < count:
        merged = []
        for start in range(0, count, width * 2):
            left = order[start:start + width]
            right = order[start + width:start + width * 2]
            i = j = 0
            while i < len(left) and j < len(right):
                result = yield base + left[i] * size, base + right[j] * size
                if result <= 0:
                    merged.append(left[i])
                    i += 1
                else:
                    merged.append(right[j])
                    j += 1
            merged.extend(left[i:])
            merged.extend(right[j:])
        order = merged
        width *= 2
    return b"".join(original[index * size:(index + 1) * size] for index in order)
