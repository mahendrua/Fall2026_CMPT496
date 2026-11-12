import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class UT_910 {
    @Test
    void testCalculatorAddition() {
        Calculator calculator = new Calculator();

        assertEquals(5, calculator.add(2, 3));
        assertEquals(0, calculator.add(-2, 2));
        assertEquals(-5, calculator.add(-2, -3));
    }
}
