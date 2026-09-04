interface apb_if;
    logic psel;
    modport slave (input psel);
endinterface

module interface_array_port (
    apb_if.slave apb [3:0]
);

endmodule
