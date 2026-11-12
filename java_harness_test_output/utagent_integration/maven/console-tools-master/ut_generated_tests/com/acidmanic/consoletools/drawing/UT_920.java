package com.acidmanic.consoletools.drawing;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class UT_920 {
    @Test
    void testSizeOperations() {
        Size size = new Size(4, 3);

        assertEquals(4, size.getColumns());
        assertEquals(3, size.getLines());

        size.setColumns(6);
        size.setLines(8);

        assertEquals(6, size.getColumns());
        assertEquals(8, size.getLines());

        Size result = size.add(new Size(2, 5));

        assertEquals(8, result.getColumns());
        assertEquals(13, result.getLines());
    }
}
