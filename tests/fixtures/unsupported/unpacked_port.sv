module unpacked_port #(
    parameter int NumWays = 4
) (
    output logic [7:0] mem       [0:15],
    output logic [7:0] mem_sized [16],
    output logic [7:0] ways      [NumWays]
);

endmodule
