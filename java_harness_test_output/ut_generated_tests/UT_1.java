import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class UT_1 {
    @Test
    void testAddition() {
        Calculator calculator = new Calculator();
        assertEquals(5, calculator.add(2, 3));
    }
}
