interface apb_if;
    logic psel;
    logic penable;
    modport slave (input psel, penable);
endinterface

module apb_target (
    input logic  pclk,
    apb_if.slave apb
);

endmodule
