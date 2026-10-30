"""CPU implementation of the OES matrix-palette vertex transformation.

Matrices use OpenGL column-major storage. Positions become eye coordinates;
normals use the weighted inverse-transpose option permitted by the extension.
https://registry.khronos.org/OpenGL/extensions/OES/OES_matrix_palette.txt
"""
IDENTITY = tuple(float(i % 5 == 0) for i in range(16))


def position(matrix, value):
    value = tuple(value) + (0.0,) * max(0, 3 - len(value))
    value = value + (1.0,) if len(value) == 3 else value
    x, y, z, w = value[:4]
    # Keep Python's sum semantics (including compensated float sums on newer
    # Python), while avoiding twenty generator resumptions per vertex.
    return (sum((matrix[0]*x, matrix[4]*y, matrix[8]*z, matrix[12]*w)),
            sum((matrix[1]*x, matrix[5]*y, matrix[9]*z, matrix[13]*w)),
            sum((matrix[2]*x, matrix[6]*y, matrix[10]*z, matrix[14]*w)),
            sum((matrix[3]*x, matrix[7]*y, matrix[11]*z, matrix[15]*w)))


def normal(matrix, value):
    a, b, c = matrix[0], matrix[4], matrix[8]
    d, e, f = matrix[1], matrix[5], matrix[9]
    g, h, i = matrix[2], matrix[6], matrix[10]
    cofactors = ((e*i-f*h, f*g-d*i, d*h-e*g),
                 (c*h-b*i, a*i-c*g, b*g-a*h),
                 (b*f-c*e, c*d-a*f, a*e-b*d))
    determinant = a * cofactors[0][0] + b * cofactors[0][1] + c * cofactors[0][2]
    if abs(determinant) < 1e-20:
        raise ValueError("singular matrix in palette normal transformation")
    return tuple(sum(row[j] * value[j] for j in range(3)) / determinant for row in cofactors)


class Palette:
    capacity = 32
    vertex_units = 3

    def __init__(self):
        self.matrices = [IDENTITY for _ in range(self.capacity)]
        self.current = 0
        self.enabled = False
        self.matrix_mode = 0x1700
        self.last_transform_count = 0

    def select(self, index):
        if not 0 <= index < self.capacity:
            raise ValueError(f"palette matrix index out of range: {index}")
        self.current = index

    def load(self, matrix):
        if len(matrix) != 16:
            raise ValueError("palette matrix must contain 16 floats")
        self.matrices[self.current] = tuple(matrix)

    def transform(self, vertex, indices, weights, vertex_normal=None):
        if not 1 <= len(indices) <= self.vertex_units or len(indices) != len(weights):
            raise ValueError("invalid matrix-palette vertex units")
        transformed = [0.0] * 4
        transformed_normal = [0.0] * 3 if vertex_normal is not None else None
        for index, weight in zip(indices, weights):
            if not 0 <= index < self.capacity:
                raise ValueError(f"vertex references invalid palette matrix: {index}")
            matrix = self.matrices[index]
            p = position(matrix, vertex)
            transformed[0] += weight * p[0]
            transformed[1] += weight * p[1]
            transformed[2] += weight * p[2]
            transformed[3] += weight * p[3]
            if transformed_normal is not None:
                for j, value in enumerate(normal(matrix, vertex_normal)):
                    transformed_normal[j] += weight * value
        return transformed, transformed_normal

    def transform_indexed(self, vertex_ids, vertices, indices, weights, normals=None, reuse=True):
        """Reuse identical indexed vertices only within this draw's stable arrays."""
        cache = {}
        result = []
        self.last_transform_count = 0
        for offset, vertex_id in enumerate(vertex_ids):
            if not reuse or vertex_id not in cache:
                value = self.transform(vertices[offset], indices[offset], weights[offset],
                                       normals[offset] if normals is not None else None)
                self.last_transform_count += 1
                if reuse:
                    cache[vertex_id] = value
            else:
                value = cache[vertex_id]
            result.append(value)
        return result
