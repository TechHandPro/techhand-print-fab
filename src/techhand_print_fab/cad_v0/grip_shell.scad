// ORIGINAL TechHand/PRINT trainer CAD v0 — training tool only
// Parametric grip shell half. Training GRIP BLOCK — not a firearm frame/slide/barrel.
// Use: side=1 (left) or side=-1 (right). L/R halves screw together with M3 + heat-set inserts.

/* ===== PARAMETERS ===== */
side = 1;                  // 1 = left half (inserts), -1 = right half (screw clearance)
grip_len = 110;            // front-back length of grip block (mm)
grip_w  = 32;              // total width when both halves mated (mm)
grip_h  = 120;             // vertical height of hand block (mm)
wall    = 2.4;             // min wall (3×0.4 nozzle)
clearance = 0.25;          // snap/screw mate clearance (mm)
insert_d = 4.1;            // M3 heat-set insert hole (~4.0–4.2 for common brass inserts)
insert_h = 5.5;            // insert boss depth (mm)
screw_clear_d = 3.3;       // M3 through-hole clearance
boss_od = 8.5;             // screw/insert boss outer diameter
laser_bay_d = 14;          // forward module-bay ID (mm); laser_clamp holds module
laser_bay_len = 38;        // forward housing length along trainer axis (mm)
spring_od = 9.5;           // spring pocket ID (~8–10 mm OD springs)
spring_pocket_l = 22;      // spring cavity length (mm)
trigger_slot_w = 9;
trigger_slot_h = 26;
$fn = 48;

half_w = grip_w / 2;

module rounded_box(sx, sy, sz, r) {
    hull() {
        for (x = [r, sx - r])
            for (y = [r, sy - r])
                for (z = [r, sz - r])
                    translate([x, y, z]) sphere(r = r);
    }
}

function boss_pts() = [
    [-grip_len * 0.30, half_w * 0.50, wall + 10],
    [ grip_len * 0.15, half_w * 0.50, wall + 10],
    [-grip_len * 0.30, half_w * 0.50, grip_h - wall - 10],
    [ grip_len * 0.15, half_w * 0.50, grip_h - wall - 10],
    [-grip_len * 0.08, half_w * 0.50, grip_h * 0.50],
    [ grip_len * 0.32, half_w * 0.50, grip_h * 0.55]
];

module grip_shell_half(which_side = 1) {
    hw = half_w;
    insert_side = (which_side > 0); // left receives heat-set inserts

    difference() {
        union() {
            // Main hand block — blocky ergonomic trainer
            translate([-grip_len / 2, 0, 0])
                rounded_box(grip_len, hw, grip_h, 7);

            // Short forward MODULE BAY (cylindrical housing, not barrel-like taper)
            bay_or = laser_bay_d / 2 + wall + 1.5;
            translate([grip_len / 2 - 6, hw / 2, grip_h * 0.55])
                rotate([0, 90, 0])
                    cylinder(h = laser_bay_len + 6, r = bay_or);

            // Screw bosses (solid, then drilled)
            for (p = boss_pts()) {
                translate([p[0], 0.8, p[2]])
                    rotate([-90, 0, 0])
                        cylinder(h = hw - 1.2, d = boss_od);
            }
        }

        // Interior cavity of grip block
        translate([-grip_len / 2 + wall, wall, wall])
            cube([grip_len - 2 * wall - 4, hw + 1, grip_h - 2 * wall]);

        // Laser bay bore
        translate([grip_len / 2 - 12, hw / 2, grip_h * 0.55])
            rotate([0, 90, 0])
                cylinder(h = laser_bay_len + 24, d = laser_bay_d);

        // Mating-face clearance shim
        translate([-grip_len / 2 - 2, -0.02, -2])
            cube([grip_len + laser_bay_len + 20, clearance, grip_h + 4]);

        // Trigger lever clearance slot (front of grip)
        translate([-6, -0.1, grip_h * 0.40])
            cube([trigger_slot_w + 2, hw + 0.2, trigger_slot_h]);

        // Momentary switch pocket
        translate([8, wall + 0.5, grip_h * 0.47])
            cube([12, 7, 6]);

        // Spring pocket for ~8–10 mm OD compression springs
        translate([5, hw * 0.40, grip_h * 0.52])
            rotate([0, 90, 0])
                cylinder(h = spring_pocket_l, d = spring_od);

        // Trigger pivot hole (M3) through half
        translate([-1, -0.1, grip_h * 0.50])
            rotate([-90, 0, 0])
                cylinder(h = hw + 0.2, d = 3.2);

        // Backstrap channel at rear
        translate([-grip_len / 2 - 0.1, wall, 10])
            cube([4.5, hw, grip_h - 20]);
        // Backstrap tab pockets
        for (z = [18, grip_h - 30]) {
            translate([-grip_len / 2 - 0.1, -0.1, z])
                cube([3.5, 3.0, 14]);
        }

        // Boss holes
        for (p = boss_pts()) {
            translate([p[0], -0.1, p[2]])
                rotate([-90, 0, 0]) {
                    if (insert_side) {
                        // through clearance from outside, insert from mating face
                        cylinder(h = hw + 0.2, d = screw_clear_d);
                        translate([0, 0, hw - insert_h - 0.5])
                            cylinder(h = insert_h + 1, d = insert_d);
                    } else {
                        cylinder(h = hw + 0.2, d = screw_clear_d);
                    }
                }
        }

        // M3 set-screw access into laser bay (right half)
        if (!insert_side) {
            translate([grip_len / 2 + laser_bay_len * 0.40, hw / 2, grip_h * 0.55])
                rotate([90, 0, 0])
                    cylinder(h = hw + 2, d = 3.2);
        }

        // Label recess on outer flank — "TRAINER" cue (cut as shallow pocket)
        translate([ -20, hw - 0.7, grip_h * 0.72])
            rotate([90, 0, 0])
                linear_extrude(1.0)
                    text("TRAINER", size = 6, halign = "center", valign = "center");
    }
}

// Render selected half; mirror right so outer faces point outward
if (side > 0) {
    grip_shell_half(1);
} else {
    mirror([0, 1, 0])
        grip_shell_half(-1);
}
