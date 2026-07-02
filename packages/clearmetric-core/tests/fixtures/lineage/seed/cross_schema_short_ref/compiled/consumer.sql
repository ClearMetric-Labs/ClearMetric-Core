select aa.label as output_value
from warehouse.pkg_a.upstream as aa
where aa.member_key > 0
